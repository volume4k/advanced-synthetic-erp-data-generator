"""Playwright-backed browser session management."""

from __future__ import annotations

from dataclasses import dataclass, field

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from erp_trace_executor.errors import SessionUserMismatchError


@dataclass
class BrowserSession:
    """Active browser session for one actor_session_id."""

    actor_session_id: str
    synthetic_actor_id: str
    context: BrowserContext
    page: Page
    fiori_messages: list[dict[str, str]] = field(default_factory=list)


class BrowserSessionManager:
    """Owns browser lifecycle and browser contexts per actor_session_id."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._actor_sessions: dict[str, BrowserSession] = {}

    def __enter__(self) -> "BrowserSessionManager":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get_session(self, *, actor_session_id: str, synthetic_actor_id: str) -> BrowserSession:
        existing = self._actor_sessions.get(actor_session_id)
        if existing is not None:
            if existing.synthetic_actor_id != synthetic_actor_id:
                raise SessionUserMismatchError(
                    f"Actor session '{actor_session_id}' is already bound to synthetic actor "
                    f"'{existing.synthetic_actor_id}', not '{synthetic_actor_id}'"
                )
            return existing

        self._ensure_browser()
        if self._browser is None:
            raise RuntimeError("Browser not initialized")

        context = self._browser.new_context()
        page = context.new_page()
        session = BrowserSession(
            actor_session_id=actor_session_id,
            synthetic_actor_id=synthetic_actor_id,
            context=context,
            page=page,
        )
        self._actor_sessions[actor_session_id] = session
        return session

    def active_session_count(self) -> int:
        return len(self._actor_sessions)

    def close(self) -> None:
        for session in self._actor_sessions.values():
            session.context.close()
        self._actor_sessions.clear()

        if self._browser is not None:
            self._browser.close()
            self._browser = None

        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._headless)
