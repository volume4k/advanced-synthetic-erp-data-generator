from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Callable

import pytest
from playwright.sync_api import Error as PlaywrightError
from pydantic import BaseModel

from erp_trace_executor import executor as executor_module
from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.canonical import CanonicalTrace
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.evidence import ExecutionEvidenceWriter
from erp_trace_executor.errors import SessionUserMismatchError
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.models import ExecutionTaskRecord, SessionInitRecord, SessionInitUser, ToolResult, returned_object
from erp_trace_executor.registry import ToolRegistry
from erp_trace_executor.tooling import ToolSpec


class ProducePurchaseRequisitionInput(BaseModel):
    pr_number: str


class ConsumePurchaseRequisitionInput(BaseModel):
    purchase_requisition: str


class NoInput(BaseModel):
    pass


def _purchase_requisition_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "material": "PUMP1902",
        "quantity": 20,
        "valuation_price": 30,
        "currency": "USD",
        "price_unit": 1,
        "delivery_date": "05/20/2026",
        "plant": "MI00",
        "purchasing_group": "N00",
        "purchasing_organization": "US00",
        "company_code": "US00",
    }
    payload.update(overrides)
    return payload


def _record(
    task_id: str,
    *,
    tool: str,
    input: dict[str, object] | None = None,
    user_id: str = "buyer-a",
    session_id: str = "buyer-session",
    case_id: str = "P2P_C001",
    line_number: int = 1,
    expected_outputs: list[str] | None = None,
) -> ExecutionTaskRecord:
    return ExecutionTaskRecord(
        task_id=task_id,
        session_id=session_id,
        user_id=user_id,
        tool=tool,
        input=input or {},
        meta={"case_id": case_id, "expected_outputs": expected_outputs or []},
        line_number=line_number,
    )


def _trace_from_records(records: list[ExecutionTaskRecord], *, run_id: str = "RUN_TEST") -> CanonicalTrace:
    sessions: dict[str, str] = {}
    cases = sorted({str(record.meta.get("case_id") or "P2P_C001") for record in records})
    for record in records:
        sessions.setdefault(record.session_id, record.user_id)

    return CanonicalTrace.model_validate(
        {
            "trace_version": "0.1",
            "run_id": run_id,
            "config_hash": "config",
            "tool_catalog_hash": "tools",
            "trace_generator_version": "0.1.0",
            "llm_metadata": {"used": False},
            "sessions": [
                {
                    "session_id": session_id,
                    "virtual_actor_id": user_id,
                    "technical_user_id": f"TU_{index:02d}",
                    "username_env_var": f"USER_{index}_UN",
                    "password_env_var": f"USER_{index}_PW",
                    "login_url_env_var": "SAP_URL",
                }
                for index, (session_id, user_id) in enumerate(sessions.items(), start=1)
            ],
            "cases": [
                {
                    "case_id": case_id,
                    "process_type": "procure_to_pay",
                    "scenario_id": "NORMAL",
                    "case_label": "normal",
                    "line_items": [],
                }
                for case_id in cases
            ],
            "dependency_graph": {
                "nodes": [
                    {
                        "node_id": record.task_id,
                        "case_id": str(record.meta.get("case_id") or "P2P_C001"),
                        "step_type": "test_step",
                        "tool_name": record.tool,
                        "virtual_actor_id": record.user_id,
                        "technical_sap_user": "TU_01",
                        "session_id": record.session_id,
                        "inputs": record.input,
                        "expected_outputs": record.meta.get("expected_outputs", []),
                        "business_dates": {},
                        "target_synthetic_time": {
                            "start": "2026-05-18T08:00:00+02:00",
                            "end": "2026-05-18T08:05:00+02:00",
                        },
                        "labels": {"step_label": "normal"},
                    }
                    for record in records
                ],
                "edges": [],
            },
            "execution_schedule": {
                "mode": "waves",
                "max_parallel_sessions": max(1, len(sessions)),
                "waves": [
                    {
                        "wave_id": "W001",
                        "sequence_no": 1,
                        "nodes": [
                            {"node_id": record.task_id, "startup_order": index}
                            for index, record in enumerate(records, start=1)
                        ],
                    }
                ],
            },
            "validation_report": {"errors": [], "warnings": []},
        }
    )


def _init_from_records(
    records: list[ExecutionTaskRecord],
    *,
    login_url: str | None = None,
    selectors: dict[str, str] | None = None,
    include_password: bool = True,
) -> SessionInitRecord:
    seen: dict[str, ExecutionTaskRecord] = {}
    for record in records:
        seen.setdefault(record.session_id, record)

    return SessionInitRecord(
        line_number=1,
        users=[
            SessionInitUser(
                session_id=record.session_id,
                user_id=record.user_id,
                username=record.user_id,
                password="secret" if include_password else None,
                login_url=login_url,
                username_selector=(selectors or {}).get("username_selector"),
                password_selector=(selectors or {}).get("password_selector"),
                submit_selector=(selectors or {}).get("submit_selector"),
                success_selector=(selectors or {}).get("success_selector"),
            )
            for record in seen.values()
        ],
    )


def _fake_login(context, params) -> ToolResult:
    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "success": True,
            "username": params.username,
            "current_url": "https://sap.example.test/home",
        },
    )


def _run_canonical_records(
    *,
    executor: TraceExecutor,
    records: list[ExecutionTaskRecord],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    context_factory: Callable[[ExecutionTaskRecord], object] | None = None,
    init: SessionInitRecord | None = None,
) -> list[ToolResult]:
    monkeypatch.setattr(executor_module, "run_login", _fake_login)
    trace = _trace_from_records(records)
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    return executor.execute_canonical(
        trace,
        init=init or _init_from_records(records),
        context_factory=context_factory or (lambda record: SimpleNamespace(record=record, **record.model_dump())),
        evidence_writer=writer,
    )


def _read_events(tmp_path) -> list[dict]:
    path = tmp_path / "RUN_TEST.execution-log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _state_test_registry(captured_inputs: list[ConsumePurchaseRequisitionInput] | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    def run_produce(context, params: ProducePurchaseRequisitionInput) -> ToolResult:
        return ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
            tool=context.tool,
            data={
                "returned_objects": [
                    returned_object("purchase_requisition", pr_number=params.pr_number)
                ],
            },
        )

    def run_consume(context, params: ConsumePurchaseRequisitionInput) -> ToolResult:
        if captured_inputs is not None:
            captured_inputs.append(params)
        return ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
            tool=context.tool,
            data={"status": "consumed"},
        )

    registry.register(
        ToolSpec(
            name="test.produce_purchase_requisition",
            input_model=ProducePurchaseRequisitionInput,
            run=run_produce,
        )
    )
    registry.register(
        ToolSpec(
            name="test.consume_purchase_requisition",
            input_model=ConsumePurchaseRequisitionInput,
            run=run_consume,
        )
    )
    return registry


def _home_reset_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        return ToolResult(
            task_id=context.record.task_id,
            session_id=context.record.session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    return registry


def test_executor_logs_unknown_tools_as_node_failure(tmp_path, monkeypatch):
    contexts: list[ExecutionTaskRecord] = []
    record = _record("task-1", tool="missing.tool")

    results = _run_canonical_records(
        executor=TraceExecutor(),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: contexts.append(item) or SimpleNamespace(record=item, **item.model_dump()),
    )

    assert [result.task_id for result in results] == ["init-login-buyer-session"]
    assert [item.task_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "node_failed" and "missing.tool" in event["error"] for event in events)


def test_executor_logs_tool_input_validation_errors(tmp_path, monkeypatch):
    contexts: list[ExecutionTaskRecord] = []
    record = _record(
        "task-1",
        tool="fiori.create_purchase_requisition",
        input=_purchase_requisition_input(quantity=0),
    )

    _run_canonical_records(
        executor=TraceExecutor(),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: contexts.append(item) or SimpleNamespace(record=item, **item.model_dump()),
    )

    assert [item.task_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "node_failed" and "Invalid input" in event["error"] for event in events)


def test_executor_resolves_process_scoped_state_variables_before_validation(tmp_path, monkeypatch):
    captured_inputs: list[ConsumePurchaseRequisitionInput] = []
    records = [
        _record(
            "C042_A1",
            tool="test.produce_purchase_requisition",
            input={"pr_number": "10000030"},
            case_id="P2P_C042",
            line_number=1,
        ),
        _record(
            "C042_A2",
            tool="test.consume_purchase_requisition",
            input={"purchase_requisition": "$purchase_requisition.pr_number"},
            case_id="P2P_C042",
            line_number=2,
        ),
    ]

    results = _run_canonical_records(
        executor=TraceExecutor(registry=_state_test_registry(captured_inputs)),
        records=records,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert [result.tool for result in results] == [
        "fiori.login",
        "test.produce_purchase_requisition",
        "test.consume_purchase_requisition",
    ]
    assert captured_inputs[0].purchase_requisition == "10000030"


def test_executor_logs_unresolved_state_variable_before_context_creation(tmp_path, monkeypatch):
    contexts: list[ExecutionTaskRecord] = []
    record = _record(
        "C042_A2",
        tool="test.consume_purchase_requisition",
        input={"purchase_requisition": "$purchase_requisition.pr_number"},
        case_id="P2P_C042",
    )

    _run_canonical_records(
        executor=TraceExecutor(registry=_state_test_registry()),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: contexts.append(item) or SimpleNamespace(record=item, **item.model_dump()),
    )

    assert [item.task_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "node_failed" and "C042_A2" in event["error"] for event in events)


def test_executor_clicks_sap_home_logo_twice_after_successful_fiori_tool(tmp_path, monkeypatch):
    page = FakeHomeResetPage(logo_click_succeeds=True)
    record = _record("task-1", tool="fiori.fake_tool")

    _run_canonical_records(
        executor=TraceExecutor(registry=_home_reset_registry()),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: FakeHomeResetContext(item, page),
    )

    assert page.logo_click_count == 2
    assert page.goto_urls == []


def test_executor_attaches_fiori_messages_to_tool_result(tmp_path, monkeypatch):
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        context.get_browser_session().fiori_messages.append(
            {
                "severity": "error",
                "text": "Geben Sie ein Rechnungsdatum ein.",
                "source": "sap-message-popover",
                "url": "https://sap.example.test/invoice",
            }
        )
        return ToolResult(
            task_id=context.record.task_id,
            session_id=context.record.session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    record = _record("task-1", tool="fiori.fake_tool")
    context = FakeMessageContext(record)

    results = _run_canonical_records(
        executor=TraceExecutor(registry=registry),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: FakeHomeResetContext(item, context.session.page)
        if item.tool == "fiori.login"
        else context,
    )

    assert results[1].data["sap_messages"] == [
        {
            "severity": "error",
            "text": "Geben Sie ein Rechnungsdatum ein.",
            "source": "sap-message-popover",
            "url": "https://sap.example.test/invoice",
        }
    ]
    assert context.session.fiori_messages == []


def test_executor_falls_back_to_current_login_url_when_home_logo_clicks_fail(tmp_path, monkeypatch):
    page = FakeHomeResetPage(logo_click_succeeds=False)
    record = _record("task-1", tool="fiori.fake_tool")

    _run_canonical_records(
        executor=TraceExecutor(registry=_home_reset_registry()),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: FakeHomeResetContext(item, page),
    )

    assert page.logo_click_count == 2
    assert page.goto_urls == ["https://sap.example.test/home"]


class FakeHomeResetContext:
    def __init__(self, record: ExecutionTaskRecord, page: "FakeHomeResetPage") -> None:
        self.record = record
        self.task_id = record.task_id
        self.session_id = record.session_id
        self.tool = record.tool
        self._session = SimpleNamespace(page=page, fiori_messages=[])

    def get_browser_session(self):
        return self._session


class FakeMessageContext:
    def __init__(self, record: ExecutionTaskRecord) -> None:
        self.record = record
        self.task_id = record.task_id
        self.session_id = record.session_id
        self.tool = record.tool
        self.session = SimpleNamespace(
            page=FakeHomeResetPage(logo_click_succeeds=True),
            fiori_messages=[],
        )

    def get_browser_session(self):
        return self.session


class FakeHomeResetPage:
    url = "https://sap.example.test/current"

    def __init__(self, *, logo_click_succeeds: bool) -> None:
        self.logo_click_succeeds = logo_click_succeeds
        self.logo_click_count = 0
        self.goto_urls: list[str] = []

    def goto(self, url: str) -> None:
        self.goto_urls.append(url)

    def locator(self, selector: str):
        return FakeHomeResetLocator(self, selector)

    def wait_for_load_state(self, _state: str, *, timeout: int | None = None) -> None:
        return None


class FakeHomeResetLocator:
    def __init__(self, page: FakeHomeResetPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def fill(self, _value: str) -> None:
        return None

    def click(self, *, timeout: int | None = None) -> None:
        if self._selector == "#login":
            return None
        if self._selector == "#shell-header-icon":
            self._page.logo_click_count += 1
            if self._page.logo_click_succeeds:
                return None
        raise PlaywrightError(f"cannot click {self._selector}")

    def is_visible(self) -> bool:
        return False


def test_browser_session_manager_reuses_session_ids():
    with BrowserSessionManager() as session_manager:
        first = session_manager.get_session(session_id="session-1", user_id="user-1")
        second = session_manager.get_session(session_id="session-1", user_id="user-1")
        other = session_manager.get_session(session_id="session-2", user_id="user-1")

        assert first is second
        assert other is not first
        assert session_manager.active_session_count() == 2


def test_browser_session_manager_rejects_mixed_users_for_same_session():
    with BrowserSessionManager() as session_manager:
        session_manager.get_session(session_id="session-1", user_id="user-1")

        with pytest.raises(SessionUserMismatchError, match="session-1"):
            session_manager.get_session(session_id="session-1", user_id="user-2")


def test_executor_runs_login_then_purchase_requisition_against_fixture_app(fixture_app_url, tmp_path):
    records = [
        _record(
            "task-1",
            tool="fiori.create_purchase_requisition",
            input=_purchase_requisition_input(quantity=3),
        )
    ]
    trace = _trace_from_records(records)
    executor = TraceExecutor()

    with BrowserSessionManager() as session_manager:
        results = executor.execute_canonical(
            trace,
            init=_fixture_init(records, fixture_app_url),
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
            evidence_writer=ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id),
        )

        assert session_manager.active_session_count() == 1

    assert [result.tool for result in results] == ["fiori.login", "fiori.create_purchase_requisition"]
    assert results[0].data["status"] == "logged_in"
    assert results[1].data["status"] == "created"
    assert results[1].data["purchase_requisition"] == "PR-0001"


def test_executor_resolves_init_passwords_from_credentials_against_fixture_app(fixture_app_url, tmp_path):
    records = [
        _record(
            "task-1",
            tool="fiori.create_purchase_requisition",
            input=_purchase_requisition_input(quantity=1),
        )
    ]
    trace = _trace_from_records(records)
    executor = TraceExecutor(credential_store=EnvCredentialStore({"buyer-a": "secret"}))

    with BrowserSessionManager() as session_manager:
        results = executor.execute_canonical(
            trace,
            init=_fixture_init(records, fixture_app_url, include_password=False),
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
            evidence_writer=ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id),
        )

    assert results[0].data["username"] == "buyer-a"
    assert results[1].data["purchase_requisition"] == "PR-0001"


def test_executor_creates_purchase_requisition_against_fixture_app(fixture_app_url, tmp_path):
    records = [
        _record(
            "task-1",
            tool="fiori.create_purchase_requisition",
            input=_purchase_requisition_input(),
            expected_outputs=["purchase_requisition.pr_number"],
        )
    ]
    trace = _trace_from_records(records)
    executor = TraceExecutor()

    with BrowserSessionManager() as session_manager:
        results = executor.execute_canonical(
            trace,
            init=_fixture_init(records, fixture_app_url),
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
            evidence_writer=ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id),
        )

    assert results[1].tool == "fiori.create_purchase_requisition"
    assert results[1].data["status"] == "created"
    assert results[1].data["purchase_requisition"] == "PR-0001"
    assert results[1].data["material"] == "PUMP1902"
    assert results[1].data["quantity"] == 20
    assert results[1].data["returned_objects"] == [
        {
            "object_type": "purchase_requisition",
            "keys": {
                "pr_number": "PR-0001",
            },
        }
    ]


def _fixture_init(
    records: list[ExecutionTaskRecord],
    fixture_app_url: str,
    *,
    include_password: bool = True,
) -> SessionInitRecord:
    return _init_from_records(
        records,
        login_url=fixture_app_url,
        include_password=include_password,
        selectors={
            "username_selector": '[data-testid="username"]',
            "password_selector": '[data-testid="password"]',
            "submit_selector": '[data-testid="login-submit"]',
            "success_selector": '[data-testid="session-user"]',
        },
    )
