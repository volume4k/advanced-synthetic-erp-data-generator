"""Canonical wave trace execution."""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Error as PlaywrightError
from pydantic import ValidationError

from erp_trace_executor.canonical import CanonicalNode, CanonicalTrace
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.evidence import ExecutionEvidenceWriter
from erp_trace_executor.errors import ToolExecutionError, ToolInputValidationError
from erp_trace_executor.fiori_page import FioriPage
from erp_trace_executor.models import ExecutionTaskRecord, SessionInitRecord, SessionInitUser, ToolResult
from erp_trace_executor.registry import ToolRegistry, build_default_registry
from erp_trace_executor.state import RuntimeStateStore
from erp_trace_executor.tools.fiori.login import LoginInput, run_login

SAP_HOME_LOGO_SELECTORS = (
    "#shell-header-icon",
)
SAP_HOME_LOGO_CLICK_ATTEMPTS = 2
SAP_HOME_NAVIGATION_TIMEOUT_MS = 1_000


class TraceExecutor:
    """Executes canonical trace nodes in scheduled wave order."""

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

    def execute_canonical(
        self,
        trace: CanonicalTrace,
        *,
        init: SessionInitRecord,
        context_factory,
        evidence_writer: ExecutionEvidenceWriter,
    ) -> list[ToolResult]:
        results: list[ToolResult] = []
        failed_cases: set[str] = set()
        nodes_by_id = {node.node_id: node for node in trace.dependency_graph.nodes}
        cases_by_id = {case.case_id: case for case in trace.cases}

        evidence_writer.log_event("run_started")
        try:
            for init_user in init.users:
                login_record = self._build_init_login_record(init_user, line_number=init.line_number)
                evidence_writer.log_event(
                    "login_started",
                    session_id=login_record.session_id,
                    virtual_actor_id=login_record.user_id,
                )
                try:
                    login_context = context_factory(login_record)
                    login_result = run_login(login_context, self._build_init_login_input(init_user))
                except Exception as exc:
                    evidence_writer.log_event(
                        "login_failed",
                        session_id=login_record.session_id,
                        virtual_actor_id=login_record.user_id,
                        error=str(exc),
                    )
                    evidence_writer.log_event("run_failed", error=str(exc))
                    raise
                results.append(login_result)
                self._remember_home_url(login_record, login_result)
                evidence_writer.log_event(
                    "login_succeeded",
                    session_id=login_record.session_id,
                    virtual_actor_id=login_record.user_id,
                )

            for wave in trace.execution_schedule.waves:
                evidence_writer.log_event("wave_started", wave_id=wave.wave_id, wave_sequence_no=wave.sequence_no)
                for scheduled_node in sorted(wave.nodes, key=lambda item: item.startup_order):
                    node = nodes_by_id[scheduled_node.node_id]
                    case = cases_by_id[node.case_id]
                    event_meta = _canonical_event_meta(
                        trace=trace,
                        node=node,
                        scenario_id=case.scenario_id,
                        wave_id=wave.wave_id,
                        wave_sequence_no=wave.sequence_no,
                        startup_order=scheduled_node.startup_order,
                    )
                    if node.case_id in failed_cases:
                        evidence_writer.log_event("node_skipped", reason="case_failed", **event_meta)
                        continue

                    evidence_writer.log_event("node_started", **event_meta)
                    record = _canonical_node_to_record(node, event_meta)
                    context = None
                    try:
                        spec, params, parent_references, context = self._prepare_canonical_node(record, context_factory)
                        result = spec.run(context, params)
                        self._attach_fiori_messages(context, result)
                        _validate_expected_outputs(node, result)
                    except Exception as exc:
                        failed_cases.add(node.case_id)
                        sap_messages = self._capture_fiori_messages(context)
                        evidence_writer.log_event(
                            "node_failed",
                            error=_safe_error_text(exc),
                            sap_messages=sap_messages,
                            **event_meta,
                        )
                        evidence_writer.log_event(
                            "case_failed",
                            error=_safe_error_text(exc),
                            sap_messages=sap_messages,
                            **event_meta,
                        )
                        if self._can_reset_home_after_failure(record, context):
                            try:
                                self._return_home(record, context)
                            except Exception as reset_exc:
                                evidence_writer.log_event(
                                    "home_reset_failed",
                                    error=_safe_error_text(reset_exc),
                                    failed_error=_safe_error_text(exc),
                                    **event_meta,
                                )
                                evidence_writer.log_event("run_failed", error=_safe_error_text(reset_exc), **event_meta)
                                raise
                        continue

                    results.append(result)
                    self._record_state_if_needed(record, result)
                    evidence_writer.log_event(
                        "state_updated",
                        object_count=len(result.data.get("returned_objects", [])),
                        **event_meta,
                    )
                    _write_object_registry_entries(
                        evidence_writer=evidence_writer,
                        node=node,
                        scenario_id=case.scenario_id,
                        result=result,
                        parent_references=parent_references,
                    )
                    self._remember_home_url(record, result)
                    self._return_home_after_tool(record, context, result)
                    evidence_writer.log_event("node_succeeded", **event_meta)
                evidence_writer.log_event("wave_completed", wave_id=wave.wave_id, wave_sequence_no=wave.sequence_no)

            evidence_writer.log_event("run_completed", failed_case_count=len(failed_cases))
            return results
        except Exception:
            raise

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def _build_init_login_record(self, init_user: SessionInitUser, *, line_number: int) -> ExecutionTaskRecord:
        return ExecutionTaskRecord(
            task_id=f"init-login-{init_user.session_id}",
            session_id=init_user.session_id,
            user_id=init_user.user_id,
            tool="fiori.login",
            input={},
            meta={"kind": "init"},
            line_number=line_number,
        )

    def _build_init_login_input(self, init_user: SessionInitUser) -> LoginInput:
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

    def _resolve_input(self, record: ExecutionTaskRecord) -> dict[str, Any]:
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

    def _record_state_if_needed(self, record: ExecutionTaskRecord, result: ToolResult) -> None:
        if not result.data.get("returned_objects"):
            return
        if result.data.get("success") is False:
            return

        case_id = self._case_id(record)
        if case_id is None:
            return

        self._state_store.record_tool_result(case_id, record.task_id, result)

    def _attach_fiori_messages(self, context: Any, result: ToolResult) -> None:
        if not hasattr(context, "get_browser_session"):
            return
        session = context.get_browser_session()

        messages = getattr(session, "fiori_messages", None)
        if not messages:
            return

        seen: set[tuple[str, str, str]] = set()
        unique_messages: list[dict[str, str]] = []
        for message in messages:
            key = (
                str(message.get("severity", "")),
                str(message.get("text", "")),
                str(message.get("source", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique_messages.append(message)

        if unique_messages:
            result.data.setdefault("sap_messages", []).extend(unique_messages)
        messages.clear()

    def _capture_fiori_messages(self, context: Any) -> list[dict[str, str]]:
        if context is None or not hasattr(context, "get_browser_session"):
            return []
        try:
            session = context.get_browser_session()
        except Exception:
            return []

        messages = getattr(session, "fiori_messages", None)
        if messages is None:
            messages = []

        try:
            FioriPage(session.page, message_sink=messages).handle_messages()
        except Exception:
            pass

        seen: set[tuple[str, str, str]] = set()
        unique_messages: list[dict[str, str]] = []
        for message in messages:
            key = (
                str(message.get("severity", "")),
                str(message.get("text", "")),
                str(message.get("source", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique_messages.append(dict(message))
        messages.clear()
        return unique_messages

    def _case_id(self, record: ExecutionTaskRecord) -> str | None:
        case_id = record.meta.get("case_id")
        return case_id if isinstance(case_id, str) and case_id else None

    def _remember_home_url(self, record: ExecutionTaskRecord, result: ToolResult) -> None:
        if record.tool != "fiori.login":
            return

        home_url = result.data.get("current_url") or result.data.get("url")
        if isinstance(home_url, str) and home_url:
            self._home_urls[record.session_id] = home_url

    def _return_home_after_tool(self, record: ExecutionTaskRecord, context: Any, result: ToolResult) -> None:
        if record.tool == "fiori.login" or not record.tool.startswith("fiori."):
            return
        if result.data.get("success") is False:
            return
        self._return_home(record, context)

    def _can_reset_home_after_failure(self, record: ExecutionTaskRecord, context: Any) -> bool:
        return record.tool != "fiori.login" and record.tool.startswith("fiori.") and context is not None

    def _return_home(self, record: ExecutionTaskRecord, context: Any) -> None:
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

    def _prepare_canonical_node(self, record: ExecutionTaskRecord, context_factory) -> tuple[Any, Any, list[dict[str, Any]], Any]:
        spec = self._registry.get(record.tool)
        resolved_input = self._resolve_input(record)
        parent_references = _parent_references(record.input, resolved_input)
        try:
            params = spec.input_model.model_validate(resolved_input)
        except ValidationError as exc:
            line_info = f" on line {record.line_number}" if record.line_number >= 0 else ""
            raise ToolInputValidationError(
                f"Invalid input for tool '{record.tool}'{line_info}: {exc}"
            ) from exc

        context = context_factory(record)
        return spec, params, parent_references, context


def _canonical_node_to_record(node: CanonicalNode, meta: dict[str, Any]) -> ExecutionTaskRecord:
    return ExecutionTaskRecord(
        task_id=node.node_id,
        session_id=node.session_id,
        user_id=node.virtual_actor_id,
        tool=node.tool_name,
        input=node.inputs,
        meta=meta,
        line_number=-1,
    )


def _canonical_event_meta(
    *,
    trace: CanonicalTrace,
    node: CanonicalNode,
    scenario_id: str,
    wave_id: str,
    wave_sequence_no: int,
    startup_order: int,
) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "wave_id": wave_id,
        "wave_sequence_no": wave_sequence_no,
        "startup_order": startup_order,
        "case_id": node.case_id,
        "node_id": node.node_id,
        "step_type": node.step_type,
        "scenario_id": scenario_id,
        "virtual_actor_id": node.virtual_actor_id,
        "technical_user_id": node.technical_sap_user,
        "session_id": node.session_id,
        "tool": node.tool_name,
        "target_synthetic_start": node.target_synthetic_time.start,
        "target_synthetic_end": node.target_synthetic_time.end,
        "expected_outputs": node.expected_outputs,
    }


def _validate_expected_outputs(node: CanonicalNode, result: ToolResult) -> None:
    returned = {
        (item.get("object_type"), key)
        for item in result.data.get("returned_objects", [])
        if isinstance(item, dict)
        for key in (item.get("keys") or {}).keys()
    }
    for expected_output in node.expected_outputs:
        parts = expected_output.split(".")
        if len(parts) != 2 or not all(parts):
            raise ToolExecutionError(f"Invalid expected output '{expected_output}' for node '{node.node_id}'")
        if (parts[0], parts[1]) not in returned:
            raise ToolExecutionError(f"Missing expected output '{expected_output}' for node '{node.node_id}'")


def _safe_error_text(exc: Exception) -> str:
    return " ".join(str(exc).split())[:2_000]


def _write_object_registry_entries(
    *,
    evidence_writer: ExecutionEvidenceWriter,
    node: CanonicalNode,
    scenario_id: str,
    result: ToolResult,
    parent_references: list[dict[str, Any]],
) -> None:
    for returned_object in result.data.get("returned_objects", []):
        if not isinstance(returned_object, dict):
            continue
        evidence_writer.record_object(
            case_id=node.case_id,
            node_id=node.node_id,
            scenario_id=scenario_id,
            virtual_actor_id=node.virtual_actor_id,
            technical_user_id=node.technical_sap_user,
            tool=node.tool_name,
            object_type=returned_object.get("object_type"),
            keys=returned_object.get("keys", {}),
            parent_references=parent_references,
            status="created",
        )


def _parent_references(raw_value: Any, resolved_value: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    _collect_parent_references(raw_value, resolved_value, references)
    return references


def _collect_parent_references(raw_value: Any, resolved_value: Any, references: list[dict[str, Any]]) -> None:
    if isinstance(raw_value, str) and raw_value.startswith("$"):
        path = raw_value[1:].split(".")
        if len(path) == 2 and all(path):
            references.append({"object_type": path[0], "key": path[1], "value": resolved_value})
        return
    if isinstance(raw_value, dict) and isinstance(resolved_value, dict):
        for key, item in raw_value.items():
            _collect_parent_references(item, resolved_value.get(key), references)
        return
    if isinstance(raw_value, list) and isinstance(resolved_value, list):
        for raw_item, resolved_item in zip(raw_value, resolved_value, strict=False):
            _collect_parent_references(raw_item, resolved_item, references)
