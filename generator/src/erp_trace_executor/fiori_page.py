"""Playwright page helpers for SAP Fiori trace tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.fiori_messages import FioriMessageHandler, FioriMessagePolicy


DEFAULT_FIORI_TIMEOUT_MS = 30_000
DEFAULT_DOM_QUIET_MS = 500
DEFAULT_NEXT_WAIT_RETRY_TIMEOUT_MS = 3_000
SETTLING_KEYS = {"Enter", "Tab"}


class FioriPage:
    """Wrap a Playwright page with SAP Fiori settle waits after risky actions.

    Tool authors can keep normal Playwright-style code such as
    ``page.get_by_role(...).click()``. The wrapper returns ``FioriLocator``
    objects that wait after actions commonly followed by UI5 rerendering or
    backend value derivation. Use ``raw_page`` as an escape hatch when a tool
    needs an unwrapped Playwright API.
    """

    def __init__(
        self,
        page: Any,
        *,
        timeout_ms: int = DEFAULT_FIORI_TIMEOUT_MS,
        quiet_ms: int = DEFAULT_DOM_QUIET_MS,
        message_sink: list[dict[str, str]] | None = None,
        message_policy: FioriMessagePolicy | None = None,
    ) -> None:
        self.raw_page = page
        self._timeout_ms = timeout_ms
        self._quiet_ms = quiet_ms
        self._retry_previous_click: Callable[[], None] | None = None
        self._message_handler = FioriMessageHandler(
            page,
            message_sink=message_sink,
            policy=message_policy,
        )

    @property
    def url(self) -> str:
        """Return current browser URL from the wrapped Playwright page."""

        return self.raw_page.url

    def goto(self, *args: Any, **kwargs: Any) -> Any:
        """Navigate with Playwright, then wait for Fiori to settle."""

        result = self.raw_page.goto(*args, **kwargs)
        self.wait_until_ready()
        return result

    def get_by_role(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a role locator that auto-settles after click-like actions."""

        return self._wrap(self.raw_page.get_by_role(*args, **kwargs))

    def get_by_label(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a label locator that auto-settles after click-like actions."""

        return self._wrap(self.raw_page.get_by_label(*args, **kwargs))

    def get_by_text(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a text locator that auto-settles after click-like actions."""

        return self._wrap(self.raw_page.get_by_text(*args, **kwargs))

    def get_by_title(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a title locator that auto-settles after click-like actions."""

        return self._wrap(self.raw_page.get_by_title(*args, **kwargs))

    def get_by_test_id(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a test-id locator, mainly for local fixtures and smoke helpers."""

        return self._wrap(self.raw_page.get_by_test_id(*args, **kwargs))

    def locator(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a CSS/XPath locator that auto-settles after click-like actions."""

        return self._wrap(self.raw_page.locator(*args, **kwargs))

    def frame_locator(self, *args: Any, **kwargs: Any) -> "FioriFrameLocator":
        """Create a frame locator whose nested locators keep Fiori wait behavior."""

        return FioriFrameLocator(self, self.raw_page.frame_locator(*args, **kwargs))

    def wait_until_ready(self) -> None:
        """Wait until common SAPUI5/Fiori async and render work has settled."""

        wait_for_fiori_settled(
            self.raw_page,
            timeout_ms=self._timeout_ms,
            quiet_ms=self._quiet_ms,
        )

    def handle_messages(self) -> list[Any]:
        """Capture and dismiss global SAP messages visible on the page."""

        return self._message_handler.handle()

    def register_retryable_click(self, retry_click: Callable[[], None]) -> None:
        """Store one click that may be replayed if the next explicit wait misses.

        Use this only for idempotent UI-opening actions, such as expanding a
        form section. Do not use it for save, submit, or purchase buttons.
        """

        self._retry_previous_click = retry_click

    def consume_retryable_click(self) -> Callable[[], None] | None:
        """Return and clear the last click allowed to retry the next wait."""

        retry_click = self._retry_previous_click
        self._retry_previous_click = None
        return retry_click

    def _wrap(self, locator: Any) -> "FioriLocator":
        return FioriLocator(self, locator)

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the raw Playwright page."""

        return getattr(self.raw_page, name)


class FioriLocator:
    """Wrap a Playwright locator and settle Fiori after state-changing actions."""

    def __init__(self, page: FioriPage, locator: Any) -> None:
        self._page = page
        self._locator = locator

    def click(self, *args: Any, **kwargs: Any) -> Any:
        """Click locator, then wait for SAPUI5 rendering and DOM churn to quiet.

        Pass ``retry_on_next_wait=True`` for safe open/navigation clicks. The
        next ``wait_for`` then probes for three seconds, replays this click once
        if the awaited object is still missing, and finally runs the normal wait.
        """

        retry_on_next_wait = bool(kwargs.pop("retry_on_next_wait", False))
        result = self._run_with_message_recovery(lambda: self._locator.click(*args, **kwargs))
        self._page.wait_until_ready()
        if retry_on_next_wait:
            self._page.register_retryable_click(lambda: self._retry_click(*args, **kwargs))
        else:
            self._page.consume_retryable_click()
        return result

    def dblclick(self, *args: Any, **kwargs: Any) -> Any:
        """Double-click locator, then wait for SAPUI5 rendering to settle."""

        result = self._run_with_message_recovery(lambda: self._locator.dblclick(*args, **kwargs))
        self._page.wait_until_ready()
        return result

    def press(self, key: str, *args: Any, **kwargs: Any) -> Any:
        """Press key and settle after Enter/Tab, which often trigger Fiori updates."""

        result = self._run_with_message_recovery(lambda: self._locator.press(key, *args, **kwargs))
        if key in SETTLING_KEYS:
            self._page.wait_until_ready()
        return result

    def fill(self, *args: Any, **kwargs: Any) -> Any:
        """Fill locator without settling; commit keys like Enter/Tab settle later."""

        return self._run_with_message_recovery(lambda: self._locator.fill(*args, **kwargs))

    def wait_for(self, *args: Any, **kwargs: Any) -> Any:
        """Wait for locator, optionally replaying one safe previous click first."""

        retry_click = self._page.consume_retryable_click()
        if retry_click is None:
            return self._run_with_message_recovery(lambda: self._locator.wait_for(*args, **kwargs))

        probe_kwargs = dict(kwargs)
        probe_kwargs["timeout"] = min(
            int(probe_kwargs.get("timeout", DEFAULT_NEXT_WAIT_RETRY_TIMEOUT_MS)),
            DEFAULT_NEXT_WAIT_RETRY_TIMEOUT_MS,
        )
        try:
            return self._run_with_message_recovery(lambda: self._locator.wait_for(*args, **probe_kwargs))
        except PlaywrightTimeoutError:
            retry_click()
            return self._run_with_message_recovery(lambda: self._locator.wait_for(*args, **kwargs))

    def inner_text(self, *args: Any, **kwargs: Any) -> str:
        """Read text from wrapped locator."""

        return self._locator.inner_text(*args, **kwargs)

    def locator(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a wrapped locator scoped below this locator."""

        return FioriLocator(self._page, self._locator.locator(*args, **kwargs))

    def get_by_text(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a wrapped text locator scoped below this locator."""

        return FioriLocator(self._page, self._locator.get_by_text(*args, **kwargs))

    def get_by_role(self, *args: Any, **kwargs: Any) -> "FioriLocator":
        """Create a wrapped role locator scoped below this locator."""

        return FioriLocator(self._page, self._locator.get_by_role(*args, **kwargs))

    @property
    def first(self) -> "FioriLocator":
        """Return first matching locator while keeping Fiori wait behavior."""

        return FioriLocator(self._page, self._locator.first)

    @property
    def content_frame(self) -> "FioriFrameLocator":
        """Return this iframe locator's content frame with wrapped locators."""

        return FioriFrameLocator(self._page, self._locator.content_frame)

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the raw Playwright locator."""

        return getattr(self._locator, name)

    def _run_with_message_recovery(self, operation: Callable[[], Any]) -> Any:
        try:
            return operation()
        except PlaywrightTimeoutError:
            messages = self._page.handle_messages()
            if not messages:
                raise
            return operation()

    def _retry_click(self, *args: Any, **kwargs: Any) -> None:
        self._run_with_message_recovery(lambda: self._locator.click(*args, **kwargs))
        self._page.wait_until_ready()


class FioriFrameLocator:
    """Wrap a Playwright frame locator with the same Fiori locator behavior."""

    def __init__(self, page: FioriPage, frame_locator: Any) -> None:
        self._page = page
        self._frame_locator = frame_locator

    def get_by_role(self, *args: Any, **kwargs: Any) -> FioriLocator:
        """Create a wrapped role locator inside this frame."""

        return FioriLocator(self._page, self._frame_locator.get_by_role(*args, **kwargs))

    def get_by_label(self, *args: Any, **kwargs: Any) -> FioriLocator:
        """Create a wrapped label locator inside this frame."""

        return FioriLocator(self._page, self._frame_locator.get_by_label(*args, **kwargs))

    def get_by_text(self, *args: Any, **kwargs: Any) -> FioriLocator:
        """Create a wrapped text locator inside this frame."""

        return FioriLocator(self._page, self._frame_locator.get_by_text(*args, **kwargs))

    def get_by_title(self, *args: Any, **kwargs: Any) -> FioriLocator:
        """Create a wrapped title locator inside this frame."""

        return FioriLocator(self._page, self._frame_locator.get_by_title(*args, **kwargs))

    def locator(self, *args: Any, **kwargs: Any) -> FioriLocator:
        """Create a wrapped CSS/XPath locator inside this frame."""

        return FioriLocator(self._page, self._frame_locator.locator(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the raw Playwright frame locator."""

        return getattr(self._frame_locator, name)


def wait_for_fiori_settled(
    page: Any,
    *,
    timeout_ms: int = DEFAULT_FIORI_TIMEOUT_MS,
    quiet_ms: int = DEFAULT_DOM_QUIET_MS,
) -> None:
    """Wait for broad Fiori readiness without pretending one signal is enough.

    This combines three cheap signals: document parsed, visible busy indicators
    gone, and the DOM quiet for a short window. Tools should still wait for
    business-specific proof, such as a visible field or generated document id.
    """

    _try_domcontentloaded(page, timeout_ms=min(timeout_ms, 10_000))
    page.wait_for_function(
        """
        () => {
            const isVisible = (element) => {
                if (!element) return false;
                const style = window.getComputedStyle(element);
                if (style.visibility === "hidden" || style.display === "none") return false;
                return Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
            };
            const busySelector = [
                ".sapUiBusy",
                ".sapUiLocalBusyIndicator",
                ".sapMBusyIndicator",
                "[aria-busy='true']"
            ].join(",");
            const hasVisibleBusy = Array.from(document.querySelectorAll(busySelector)).some(isVisible);
            let uiDirty = false;
            try {
                const core = window.sap?.ui?.getCore?.();
                uiDirty = Boolean(core?.getUIDirty?.());
            } catch {
                uiDirty = false;
            }
            return !hasVisibleBusy && !uiDirty;
        }
        """,
        timeout=timeout_ms,
    )
    page.wait_for_function(
        """
        ({ quietMs }) => new Promise((resolve) => {
            if (!document.body) {
                resolve(true);
                return;
            }

            let timer;
            const observer = new MutationObserver(() => {
                clearTimeout(timer);
                timer = setTimeout(done, quietMs);
            });
            const done = () => {
                observer.disconnect();
                resolve(true);
            };

            observer.observe(document.body, {
                attributes: true,
                childList: true,
                subtree: true
            });
            timer = setTimeout(done, quietMs);
        })
        """,
        arg={"quietMs": quiet_ms},
        timeout=timeout_ms,
    )


def _try_domcontentloaded(page: Any, *, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return
