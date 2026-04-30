from __future__ import annotations

from typing import Any

from erp_trace_executor.fiori_page import FioriPage


class FakeLocator:
    def __init__(self, page: "FakePage", name: str) -> None:
        self._page = page
        self._name = name

    def click(self) -> None:
        self._page.actions.append(("click", self._name))

    def fill(self, value: str) -> None:
        self._page.actions.append(("fill", self._name, value))

    def press(self, key: str) -> None:
        self._page.actions.append(("press", self._name, key))


class FakePage:
    url = "https://example.test/fiori"

    def __init__(self) -> None:
        self.actions: list[tuple[Any, ...]] = []

    def get_by_role(self, role: str, *, name: str) -> FakeLocator:
        return FakeLocator(self, f"role:{role}:{name}")

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.actions.append(("wait_for_load_state", state, timeout))

    def wait_for_function(self, expression: str, **kwargs: Any) -> None:
        self.actions.append(("wait_for_function", "quietMs" in str(kwargs.get("arg")), kwargs.get("timeout")))


def test_fiori_locator_click_waits_for_page_to_settle():
    raw_page = FakePage()
    page = FioriPage(raw_page, timeout_ms=1234, quiet_ms=222)

    page.get_by_role("button", name="Bestellen").click()

    assert raw_page.actions[0] == ("click", "role:button:Bestellen")
    assert raw_page.actions[1] == ("wait_for_load_state", "domcontentloaded", 1234)
    assert raw_page.actions[2] == ("wait_for_function", False, 1234)
    assert raw_page.actions[3] == ("wait_for_function", True, 1234)


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
