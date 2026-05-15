"""Execution context passed to tools."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TypeVar

from erp_trace_executor.browser.session import BrowserSession, BrowserSessionManager
from erp_trace_executor.fiori_page import FioriPage
from erp_trace_executor.models import ExecutionTaskRecord

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class ExecutionContext:
    """Task-scoped context with access to the shared session manager."""

    record: ExecutionTaskRecord
    session_manager: BrowserSessionManager

    @property
    def planned_step_id(self) -> str:
        return self.record.planned_step_id

    @property
    def actor_session_id(self) -> str:
        return self.record.actor_session_id

    @property
    def tool(self) -> str:
        return self.record.tool

    def submit_in_actor_session(self, operation: Callable[["ActorSessionExecutionContext"], ResultT]) -> Future[ResultT]:
        return self.session_manager.submit_for_session(
            actor_session_id=self.record.actor_session_id,
            synthetic_actor_id=self.record.synthetic_actor_id,
            operation=lambda session: operation(ActorSessionExecutionContext(record=self.record, session=session)),
        )

    def run_in_actor_session(self, operation: Callable[["ActorSessionExecutionContext"], ResultT]) -> ResultT:
        return self.submit_in_actor_session(operation).result()

    def get_browser_session(self) -> BrowserSession:
        return self.session_manager.get_session(
            actor_session_id=self.record.actor_session_id,
            synthetic_actor_id=self.record.synthetic_actor_id,
        )

    def get_fiori_page(self) -> FioriPage:
        """Return current session page wrapped with Fiori-aware settle waits."""

        session = self.get_browser_session()
        return FioriPage(session.page, message_sink=session.fiori_messages)


@dataclass(frozen=True)
class ActorSessionExecutionContext:
    """Task context bound to a worker-owned actor session."""

    record: ExecutionTaskRecord
    session: BrowserSession

    @property
    def planned_step_id(self) -> str:
        return self.record.planned_step_id

    @property
    def actor_session_id(self) -> str:
        return self.record.actor_session_id

    @property
    def tool(self) -> str:
        return self.record.tool

    def get_browser_session(self) -> BrowserSession:
        return self.session

    def get_fiori_page(self) -> FioriPage:
        return FioriPage(self.session.page, message_sink=self.session.fiori_messages)
