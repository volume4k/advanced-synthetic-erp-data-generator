"""Page-object helpers for the local Fiori fixture app."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from erp_trace_executor.errors import ToolExecutionError


class FixtureFioriPage:
    """Wraps selectors and flows for the test fixture app."""

    def __init__(self, page: Page) -> None:
        self._page = page

    def goto(self, base_url: str) -> None:
        self._page.goto(base_url)

    def login(self, username: str, password: str) -> None:
        self._page.get_by_test_id("username").fill(username)
        self._page.get_by_test_id("password").fill(password)
        self._page.get_by_test_id("login-submit").click()
        expect(self._page.get_by_test_id("session-user")).to_have_text(username)

    def ensure_logged_in(self, expected_username: str) -> None:
        if not self._page.get_by_test_id("session-shell").is_visible():
            raise ToolExecutionError("The current browser session is not logged in")
        expect(self._page.get_by_test_id("session-user")).to_have_text(expected_username)

    def create_order(self, item_name: str, quantity: int) -> dict[str, str | int]:
        self._page.get_by_test_id("item-name").fill(item_name)
        self._page.get_by_test_id("item-quantity").fill(str(quantity))
        self._page.get_by_test_id("order-submit").click()
        summary = self._page.get_by_test_id("latest-order")
        expect(summary).to_have_text(f"{item_name}:{quantity}")
        order_count = int(self._page.get_by_test_id("order-count").inner_text())
        return {
            "item_name": item_name,
            "quantity": quantity,
            "order_count": order_count,
            "latest_order": summary.inner_text(),
        }
