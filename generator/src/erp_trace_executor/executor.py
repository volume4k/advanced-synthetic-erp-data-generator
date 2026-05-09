"""Sequential trace execution."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Error as PlaywrightError
from pydantic import ValidationError

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.errors import SessionUserMismatchError, ToolExecutionError, ToolInputValidationError
from erp_trace_executor.models import ToolResult, TraceDefinition, TraceInitUser, TraceRecord
from erp_trace_executor.registry import ToolRegistry, build_default_registry
from erp_trace_executor.state import RuntimeStateStore
from erp_trace_executor.tools.fiori.login import LoginInput, run_login

SAP_HOME_LOGO_SELECTORS = (
    "#shell-header-icon",
)
SAP_HOME_LOGO_CLICK_ATTEMPTS = 2
SAP_HOME_NAVIGATION_TIMEOUT_MS = 1_000


class TraceExecutor:
    """Executes validated trace records in file order."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        credential_store: EnvCredentialStore | None = None,
        state_store: RuntimeStateStore | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._credential_store = credential_store or EnvCredentialStore()
        self._state_store = state_store or RuntimeStateStore()
        self._home_urls: dict[str, str] = {}

    def execute(self, trace: TraceDefinition | list[TraceRecord], context_factory) -> list[ToolResult]:
        results: list[ToolResult] = []
        records = trace.tasks if isinstance(trace, TraceDefinition) else trace
        init_sessions: dict[str, str] = {}

        if isinstance(trace, TraceDefinition) and trace.init is not None:
            for init_user in trace.init.users:
                init_sessions[init_user.session_id] = init_user.user_id
            for record in records:
                if record.tool != "fiori.login":
                    self._ensure_task_uses_initialized_session(record, init_sessions)

            for init_user in trace.init.users:
                login_record = self._build_init_login_record(init_user, line_number=trace.init.line_number)
                login_context = context_factory(login_record)
                login_result = run_login(login_context, self._build_init_login_input(init_user))
                results.append(login_result)
                self._remember_home_url(login_record, login_result)

        for record in records:
            spec = self._registry.get(record.tool)
            resolved_input = self._resolve_input(record)
            try:
                params = spec.input_model.model_validate(resolved_input)
            except ValidationError as exc:
                raise ToolInputValidationError(
                    f"Invalid input for tool '{record.tool}' on line {record.line_number}: {exc}"
                ) from exc

            context = context_factory(record)
            result = spec.run(context, params)
            results.append(result)
            self._record_state_if_needed(record, result)
            self._remember_home_url(record, result)
            self._return_home_after_tool(record, context, result)

        return results

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def _build_init_login_record(self, init_user: TraceInitUser, *, line_number: int) -> TraceRecord:
        return TraceRecord(
            task_id=f"init-login-{init_user.session_id}",
            session_id=init_user.session_id,
            user_id=init_user.user_id,
            tool="fiori.login",
            input={},
            meta={"kind": "init"},
            line_number=line_number,
        )

    def _build_init_login_input(self, init_user: TraceInitUser) -> LoginInput:
        password = init_user.password or self._credential_store.password_for_username(init_user.username)
        payload = {
            "url": init_user.login_url,
            "username": init_user.username,
            "password": password,
            "username_selector": init_user.username_selector,
            "password_selector": init_user.password_selector,
            "submit_selector": init_user.submit_selector,
            "success_selector": init_user.success_selector,
        }
        return LoginInput.model_validate({key: value for key, value in payload.items() if value is not None})

    def _ensure_task_uses_initialized_session(self, record: TraceRecord, init_sessions: dict[str, str]) -> None:
        expected_user = init_sessions.get(record.session_id)
        if expected_user is None:
            raise SessionUserMismatchError(
                f"Task '{record.task_id}' uses session '{record.session_id}' that was not initialized"
            )
        if expected_user != record.user_id:
            raise SessionUserMismatchError(
                f"Task '{record.task_id}' uses session '{record.session_id}' for user '{record.user_id}', "
                f"but init bound it to user '{expected_user}'"
            )

    def _resolve_input(self, record: TraceRecord) -> dict[str, Any]:
        case_id = self._case_id(record)
        return self._resolve_value(record.input, case_id=case_id, task_id=record.task_id)

    def _resolve_value(self, value: Any, *, case_id: str | None, task_id: str) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self._state_store.resolve(case_id, value, task_id=task_id)
        if isinstance(value, dict):
            return {key: self._resolve_value(item, case_id=case_id, task_id=task_id) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve_value(item, case_id=case_id, task_id=task_id) for item in value]
        return value

    def _record_state_if_needed(self, record: TraceRecord, result: ToolResult) -> None:
        if not result.data.get("returned_objects"):
            return
        if result.data.get("success") is False:
            return

        case_id = self._case_id(record)
        if case_id is None:
            return

        self._state_store.record_tool_result(case_id, record.task_id, result)

    def _case_id(self, record: TraceRecord) -> str | None:
        case_id = record.meta.get("case_id")
        return case_id if isinstance(case_id, str) and case_id else None

    def _remember_home_url(self, record: TraceRecord, result: ToolResult) -> None:
        if record.tool != "fiori.login":
            return

        home_url = result.data.get("url") or result.data.get("current_url")
        if isinstance(home_url, str) and home_url:
            self._home_urls[record.session_id] = home_url

    def _return_home_after_tool(self, record: TraceRecord, context: Any, result: ToolResult) -> None:
        if record.tool == "fiori.login" or not record.tool.startswith("fiori."):
            return
        if result.data.get("success") is False:
            return
        if not hasattr(context, "get_browser_session"):
            return

        page = context.get_browser_session().page
        logo_clicked = False
        for _attempt in range(SAP_HOME_LOGO_CLICK_ATTEMPTS):
            logo_clicked = self._click_sap_home_logo_once(page) or logo_clicked

        if logo_clicked:
            return

        home_url = self._home_urls.get(record.session_id)
        if home_url is None:
            raise ToolExecutionError(
                f"Could not return session '{record.session_id}' to SAP home after task '{record.task_id}': "
                "SAP logo click failed and no home URL is known"
            )

        page.goto(home_url)
        self._wait_for_home_navigation(page)

    def _click_sap_home_logo_once(self, page: Any) -> bool:
        for selector in SAP_HOME_LOGO_SELECTORS:
            try:
                page.locator(selector).click(timeout=SAP_HOME_NAVIGATION_TIMEOUT_MS)
                self._wait_for_home_navigation(page)
                return True
            except (PlaywrightError, AttributeError):
                continue
        return False

    def _wait_for_home_navigation(self, page: Any) -> None:
        try:
            page.wait_for_load_state("load", timeout=SAP_HOME_NAVIGATION_TIMEOUT_MS)
        except (PlaywrightError, AttributeError):
            return
