"""Execution context passed to tools."""

from __future__ import annotations

from dataclasses import dataclass

from erp_trace_executor.browser.session import BrowserSession, BrowserSessionManager
from erp_trace_executor.fiori_page import FioriPage
from erp_trace_executor.models import ExecutionTaskRecord


@dataclass(frozen=True)
class ExecutionContext:
    """Task-scoped context with access to the shared session manager."""

    record: ExecutionTaskRecord
    session_manager: BrowserSessionManager

    def get_browser_session(self) -> BrowserSession:
        return self.session_manager.get_session(
            actor_session_id=self.record.actor_session_id,
            synthetic_actor_id=self.record.synthetic_actor_id,
        )

    def get_fiori_page(self) -> FioriPage:
        """Return current session page wrapped with Fiori-aware settle waits."""

        session = self.get_browser_session()
        return FioriPage(session.page, message_sink=session.fiori_messages)
