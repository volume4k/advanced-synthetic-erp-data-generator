from __future__ import annotations

from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.fiori_page import FioriPage
from erp_trace_executor.fiori_messages import FioriMessagePolicy


class FakeLocator:
    def __init__(self, page: "FakePage", name: str) -> None:
        self._page = page
        self._name = name

    def click(self, **_kwargs: Any) -> None:
        self._page.actions.append(("click", self._name))

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
    assert ("evaluate_messages",) in raw_page.actions


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


def test_fiori_page_captures_and_dismisses_messages_before_blockable_actions():
    raw_page = FakePage()
    raw_page.messages = [
        {
            "severity": "error",
            "text": "Geben Sie ein Rechnungsdatum ein.",
            "source": "sap-message-popover",
        }
    ]
    captured: list[dict[str, str]] = []
    page = FioriPage(raw_page, message_sink=captured, message_policy=FioriMessagePolicy())

    page.get_by_role("textbox", name="Bruttobetrag").fill("200.00")

    assert captured[0]["text"] == "Geben Sie ein Rechnungsdatum ein."
    assert ("click", "role:button:Schließen") in raw_page.actions
