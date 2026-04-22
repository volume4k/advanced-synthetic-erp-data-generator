"""Execution context passed to tools."""

from __future__ import annotations

from dataclasses import dataclass

from erp_trace_executor.browser.session import BrowserSession, BrowserSessionManager
from erp_trace_executor.models import TraceRecord


@dataclass(frozen=True)
class ExecutionContext:
    """Task-scoped context with access to the shared session manager."""

    record: TraceRecord
    session_manager: BrowserSessionManager

    def get_browser_session(self) -> BrowserSession:
        return self.session_manager.get_session(
            session_id=self.record.session_id,
            user_id=self.record.user_id,
        )
