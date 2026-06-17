"""Playwright-backed browser session management."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from concurrent.futures import Future
from dataclasses import dataclass, field
import queue
import threading
from typing import TypeVar

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from erp_trace_executor.errors import SessionUserMismatchError

ResultT = TypeVar("ResultT")


@dataclass
class BrowserSession:
    """Active browser session for one actor_session_id."""

    actor_session_id: str
    synthetic_actor_id: str
    context: BrowserContext
    page: Page
    fiori_messages: list[dict[str, str]] = field(default_factory=list)


class BrowserSessionManager:
    """Owns worker-scoped browser lifecycle per actor_session_id."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._lock = threading.RLock()
        self._workers: dict[str, _ActorSessionWorker] = {}

    def __enter__(self) -> "BrowserSessionManager":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def submit_for_session(
        self,
        *,
        actor_session_id: str,
        synthetic_actor_id: str,
        operation: Callable[[BrowserSession], ResultT],
    ) -> Future[ResultT]:
        worker = self._worker_for(actor_session_id=actor_session_id, synthetic_actor_id=synthetic_actor_id)
        return worker.submit(operation)

    def run_for_session(
        self,
        *,
        actor_session_id: str,
        synthetic_actor_id: str,
        operation: Callable[[BrowserSession], ResultT],
    ) -> ResultT:
        return self.submit_for_session(
            actor_session_id=actor_session_id,
            synthetic_actor_id=synthetic_actor_id,
            operation=operation,
        ).result()

    def get_session(self, *, actor_session_id: str, synthetic_actor_id: str) -> BrowserSession:
        with self._lock:
            worker = self._workers.get(actor_session_id)
            if worker is None:
                raise RuntimeError("Actor session is worker-owned; use run_for_session or submit_for_session")
            if worker.synthetic_actor_id != synthetic_actor_id:
                raise SessionUserMismatchError(
                    f"Actor session '{actor_session_id}' is already bound to synthetic actor "
                    f"'{worker.synthetic_actor_id}', not '{synthetic_actor_id}'"
                )
        return worker.current_session()

    def active_session_count(self) -> int:
        with self._lock:
            return len(self._workers)

    def close(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()

        for worker in workers:
            worker.close()

    def _worker_for(self, *, actor_session_id: str, synthetic_actor_id: str) -> "_ActorSessionWorker":
        with self._lock:
            worker = self._workers.get(actor_session_id)
            if worker is not None:
                if worker.synthetic_actor_id != synthetic_actor_id:
                    raise SessionUserMismatchError(
                        f"Actor session '{actor_session_id}' is already bound to synthetic actor "
                        f"'{worker.synthetic_actor_id}', not '{synthetic_actor_id}'"
                    )
                return worker

            worker = _ActorSessionWorker(
                actor_session_id=actor_session_id,
                synthetic_actor_id=synthetic_actor_id,
                headless=self._headless,
            )
            self._workers[actor_session_id] = worker
            return worker


@dataclass
class _WorkerRequest:
    operation: Callable[[BrowserSession], object] | None
    future: Future[object]


class _ActorSessionWorker:
    def __init__(self, *, actor_session_id: str, synthetic_actor_id: str, headless: bool) -> None:
        self.actor_session_id = actor_session_id
        self.synthetic_actor_id = synthetic_actor_id
        self._headless = headless
        self._requests: queue.Queue[_WorkerRequest] = queue.Queue()
        self._thread_id: int | None = None
        self._session: BrowserSession | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"erp-trace-actor-session-{actor_session_id}",
            daemon=True,
        )
        self._thread.start()

    def submit(self, operation: Callable[[BrowserSession], ResultT]) -> Future[ResultT]:
        future: Future[ResultT] = Future()
        self._requests.put(_WorkerRequest(operation=operation, future=future))
        return future

    def current_session(self) -> BrowserSession:
        if threading.get_ident() != self._thread_id:
            raise RuntimeError("Actor session is worker-owned; use run_for_session or submit_for_session")
        if self._session is None:
            raise RuntimeError("Actor session is not initialized")
        return self._session

    def close(self) -> None:
        future: Future[object] = Future()
        self._requests.put(_WorkerRequest(operation=None, future=future))
        future.result()
        self._thread.join()

    def _run(self) -> None:
        self._thread_id = threading.get_ident()
        playwright: Playwright | None = None
        browser: Browser | None = None
        try:
            while True:
                request = self._requests.get()
                if request.operation is None:
                    try:
                        self._close_resources(browser=browser, playwright=playwright)
                        browser = None
                        playwright = None
                    except Exception as exc:
                        request.future.set_exception(exc)
                    else:
                        request.future.set_result(None)
                    return

                if not request.future.set_running_or_notify_cancel():
                    continue
                context: BrowserContext | None = None
                try:
                    if self._session is None:
                        playwright = sync_playwright().start()
                        browser = playwright.chromium.launch(headless=self._headless)
                        context = browser.new_context()
                        page = context.new_page()
                        self._session = BrowserSession(
                            actor_session_id=self.actor_session_id,
                            synthetic_actor_id=self.synthetic_actor_id,
                            context=context,
                            page=page,
                        )
                        context = None
                    request.future.set_result(request.operation(self._session))
                except Exception as exc:
                    if self._session is None:
                        self._discard_partial_resources(context=context, browser=browser, playwright=playwright)
                        context = None
                        browser = None
                        playwright = None
                    request.future.set_exception(exc)
                except BaseException as exc:
                    request.future.set_exception(exc)
                    raise
        finally:
            self._close_resources(browser=browser, playwright=playwright)

    def _discard_partial_resources(
        self,
        *,
        context: BrowserContext | None,
        browser: Browser | None,
        playwright: Playwright | None,
    ) -> None:
        if context is not None:
            with suppress(Exception):
                context.close()

        if browser is not None:
            with suppress(Exception):
                browser.close()

        if playwright is not None:
            with suppress(Exception):
                playwright.stop()

    def _close_resources(self, *, browser: Browser | None, playwright: Playwright | None) -> None:
        if self._session is not None:
            with suppress(Exception):
                self._session.context.close()
            self._session = None

        if browser is not None:
            with suppress(Exception):
                browser.close()

        if playwright is not None:
            with suppress(Exception):
                playwright.stop()
