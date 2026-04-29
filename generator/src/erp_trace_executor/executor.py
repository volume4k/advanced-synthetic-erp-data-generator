"""Sequential trace execution."""

from __future__ import annotations

from pydantic import ValidationError

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.errors import SessionUserMismatchError, ToolInputValidationError
from erp_trace_executor.models import ToolResult, TraceDefinition, TraceInitUser, TraceRecord
from erp_trace_executor.registry import ToolRegistry, build_default_registry
from erp_trace_executor.tools.fiori.login import LoginInput, run_login


class TraceExecutor:
    """Executes validated trace records in file order."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        credential_store: EnvCredentialStore | None = None,
    ) -> None:
        self._registry = registry or build_default_registry()
        self._credential_store = credential_store or EnvCredentialStore()

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
                results.append(run_login(login_context, self._build_init_login_input(init_user)))

        for record in records:
            spec = self._registry.get(record.tool)
            try:
                params = spec.input_model.model_validate(record.input)
            except ValidationError as exc:
                raise ToolInputValidationError(
                    f"Invalid input for tool '{record.tool}' on line {record.line_number}: {exc}"
                ) from exc

            context = context_factory(record)
            results.append(spec.run(context, params))

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
