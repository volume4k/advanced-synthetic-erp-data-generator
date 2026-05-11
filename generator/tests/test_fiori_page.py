from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.fiori_page import FioriPage
from erp_trace_executor.fiori_messages import FioriMessagePolicy


class FakeLocator:
    def __init__(self, page: "FakePage", name: str) -> None:
        self._page = page
        self._name = name

    def click(self, **_kwargs: Any) -> None:
        self._page.actions.append(("click", self._name))
        failures_remaining = self._page.click_failures_by_name.get(self._name, 0)
        if failures_remaining > 0:
            self._page.click_failures_by_name[self._name] = failures_remaining - 1
            raise PlaywrightTimeoutError(f"cannot click {self._name}")

    def fill(self, value: str) -> None:
        self._page.actions.append(("fill", self._name, value))

    def press(self, key: str) -> None:
        self._page.actions.append(("press", self._name, key))

    def wait_for(self, **kwargs: Any) -> None:
        self._page.actions.append(("wait_for", self._name, kwargs.get("timeout")))
        if self._page.wait_failures_remaining > 0:
            self._page.wait_failures_remaining -= 1
            raise PlaywrightTimeoutError("missing")

    def get_by_role(self, role: str, *, name: str) -> "FakeLocator":
        return FakeLocator(self._page, f"{self._name}->role:{role}:{name}")


class FakePage:
    url = "https://example.test/fiori"

    def __init__(self) -> None:
        self.actions: list[tuple[Any, ...]] = []
        self.wait_failures_remaining = 0
        self.click_failures_by_name: dict[str, int] = {}
        self.messages: list[dict[str, str]] = []

    def get_by_role(self, role: str, *, name: str) -> FakeLocator:
        return FakeLocator(self, f"role:{role}:{name}")

    def get_by_title(self, title: str) -> FakeLocator:
        return FakeLocator(self, f"title:{title}")

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.actions.append(("wait_for_load_state", state, timeout))

    def wait_for_function(self, expression: str, **kwargs: Any) -> None:
        self.actions.append(("wait_for_function", "quietMs" in str(kwargs.get("arg")), kwargs.get("timeout")))

    def evaluate(self, _script: str) -> list[dict[str, str]]:
        self.actions.append(("evaluate_messages",))
        return self.messages

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, f"locator:{selector}")


def test_fiori_locator_click_waits_for_page_to_settle():
    raw_page = FakePage()
    page = FioriPage(raw_page, timeout_ms=1234, quiet_ms=222)

    page.get_by_role("button", name="Bestellen").click()

    assert ("click", "role:button:Bestellen") in raw_page.actions
    assert ("wait_for_load_state", "domcontentloaded", 1234) in raw_page.actions
    assert ("wait_for_function", False, 1234) in raw_page.actions
    assert ("wait_for_function", True, 1234) in raw_page.actions
    assert ("evaluate_messages",) not in raw_page.actions


def test_fiori_locator_press_settles_only_for_commit_keys():
    raw_page = FakePage()
    page = FioriPage(raw_page)

    page.get_by_role("textbox", name="Material").fill("PUMP1902")
    page.get_by_role("textbox", name="Material").press("A")
    page.get_by_role("textbox", name="Material").press("Enter")

    assert ("fill", "role:textbox:Material", "PUMP1902") in raw_page.actions
    assert ("press", "role:textbox:Material", "A") in raw_page.actions
    assert raw_page.actions.count(("wait_for_function", False, 30_000)) == 1
    assert raw_page.actions.count(("wait_for_function", True, 30_000)) == 1
    assert ("evaluate_messages",) not in raw_page.actions


def test_fiori_locator_replays_retryable_click_when_next_wait_misses():
    raw_page = FakePage()
    raw_page.wait_failures_remaining = 1
    page = FioriPage(raw_page)

    page.get_by_role("button", name="Position anlegen").click(retry_on_next_wait=True)
    page.get_by_role("textbox", name="Material").wait_for(state="visible")

    assert raw_page.actions.count(("click", "role:button:Position anlegen")) == 2
    assert ("wait_for", "role:textbox:Material", 3000) in raw_page.actions
    assert ("wait_for", "role:textbox:Material", None) in raw_page.actions


def test_fiori_locator_wraps_scoped_role_locators():
    raw_page = FakePage()
    page = FioriPage(raw_page)

    page.get_by_role("row", name="5105600103").get_by_role("button", name="Ausgleichen").click()

    assert (
        "click",
        "role:row:5105600103->role:button:Ausgleichen",
    ) in raw_page.actions


def test_fiori_page_captures_and_dismisses_messages_after_action_timeout_then_retries():
    raw_page = FakePage()
    raw_page.click_failures_by_name["role:button:Buchen"] = 1
    raw_page.messages = [
        {
            "severity": "error",
            "text": "Geben Sie ein Rechnungsdatum ein.",
            "source": "sap-message-popover",
        }
    ]
    captured: list[dict[str, str]] = []
    page = FioriPage(raw_page, message_sink=captured, message_policy=FioriMessagePolicy())

    page.get_by_role("button", name="Buchen").click()

    assert captured[0]["text"] == "Geben Sie ein Rechnungsdatum ein."
    assert ("click", "role:button:Schließen") in raw_page.actions
    assert raw_page.actions.count(("click", "role:button:Buchen")) == 2


def test_fiori_page_re_raises_action_timeout_when_no_message_is_visible():
    raw_page = FakePage()
    raw_page.click_failures_by_name["role:button:Buchen"] = 1
    page = FioriPage(raw_page)

    with pytest.raises(PlaywrightTimeoutError, match="Buchen"):
        page.get_by_role("button", name="Buchen").click()

    assert ("evaluate_messages",) in raw_page.actions
    assert ("click", "role:button:Schließen") not in raw_page.actions
    assert raw_page.actions.count(("click", "role:button:Buchen")) == 1


def test_fiori_page_raises_fatal_message_after_action_timeout():
    raw_page = FakePage()
    raw_page.click_failures_by_name["role:button:Buchen"] = 1
    raw_page.messages = [
        {
            "severity": "error",
            "text": "App konnte wegen technischem Fehler nicht geöffnet werden.",
            "source": "sap-message-popover",
        }
    ]
    policy = FioriMessagePolicy(fatal_patterns=(r"technischem Fehler",))
    page = FioriPage(raw_page, message_policy=policy)

    with pytest.raises(ToolExecutionError, match="technischem Fehler"):
        page.get_by_role("button", name="Buchen").click()

    assert raw_page.actions.count(("click", "role:button:Buchen")) == 1


def test_fiori_page_does_not_dismiss_anything_for_hidden_navigation_timeout_without_messages():
    raw_page = FakePage()
    raw_page.click_failures_by_name["title:Navigation"] = 1
    page = FioriPage(raw_page)

    with pytest.raises(PlaywrightTimeoutError, match="Navigation"):
        page.get_by_title("Navigation").click()

    assert ("evaluate_messages",) in raw_page.actions
    assert ("click", "role:button:Schließen") not in raw_page.actions
    assert ("click", "title:Entfernen") not in raw_page.actions
