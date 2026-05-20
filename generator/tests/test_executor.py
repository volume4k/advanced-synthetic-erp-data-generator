from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading
from types import SimpleNamespace
from typing import Callable

import pytest
from playwright.sync_api import Error as PlaywrightError
from pydantic import BaseModel

from erp_trace_executor import executor as executor_module
from erp_trace_executor.browser import session as session_module
from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.canonical import CanonicalTrace
from erp_trace_executor.context import ActorSessionExecutionContext, ExecutionContext
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
    planned_step_id: str,
    *,
    tool: str,
    input: dict[str, object] | None = None,
    synthetic_actor_id: str = "buyer-a",
    actor_session_id: str = "buyer-session",
    case_id: str = "P2P_C001",
    line_number: int = 1,
    required_sap_object_keys: list[str] | None = None,
) -> ExecutionTaskRecord:
    return ExecutionTaskRecord(
        planned_step_id=planned_step_id,
        actor_session_id=actor_session_id,
        synthetic_actor_id=synthetic_actor_id,
        tool=tool,
        input=input or {},
        meta={"case_id": case_id, "required_sap_object_keys": required_sap_object_keys or []},
        line_number=line_number,
    )


def _trace_from_records(records: list[ExecutionTaskRecord], *, run_id: str = "RUN_TEST") -> CanonicalTrace:
    actor_sessions: dict[str, str] = {}
    cases = sorted({str(record.meta.get("case_id") or "P2P_C001") for record in records})
    for record in records:
        actor_sessions.setdefault(record.actor_session_id, record.synthetic_actor_id)
    technical_sap_user_ids_by_session = {
        actor_session_id: f"TU_{index:02d}"
        for index, actor_session_id in enumerate(actor_sessions, start=1)
    }

    return CanonicalTrace.model_validate(
        {
            "trace_version": "0.2",
            "run_id": run_id,
            "config_hash": "config",
            "tool_catalog_hash": "tools",
            "trace_generator_version": "0.1.0",
            "llm_metadata": {"used": False},
            "actor_sessions": [
                {
                    "actor_session_id": actor_session_id,
                    "synthetic_actor_id": synthetic_actor_id,
                    "technical_sap_user_id": technical_sap_user_ids_by_session[actor_session_id],
                    "username_env_var": f"USER_{index}_UN",
                    "password_env_var": f"USER_{index}_PW",
                    "login_url_env_var": "SAP_URL",
                }
                for index, (actor_session_id, synthetic_actor_id) in enumerate(actor_sessions.items(), start=1)
            ],
            "cases": [
                {
                    "case_id": case_id,
                    "process_type": "procure_to_pay",
                    "case_scenario_type": "NORMAL",
                    "line_items": [],
                }
                for case_id in cases
            ],
            "dependency_graph": {
                "planned_steps": [
                    {
                        "planned_step_id": record.planned_step_id,
                        "case_id": str(record.meta.get("case_id") or "P2P_C001"),
                        "step_type": "test_step",
                        "tool_name": record.tool,
                        "synthetic_actor_id": record.synthetic_actor_id,
                        "technical_sap_user_id": technical_sap_user_ids_by_session[record.actor_session_id],
                        "actor_session_id": record.actor_session_id,
                        "inputs": record.input,
                        "required_sap_object_keys": record.meta.get("required_sap_object_keys", []),
                        "planned_date_inputs": {},
                        "planned_synthetic_time": {
                            "start": "2026-05-18T08:00:00+02:00",
                            "end": "2026-05-18T08:05:00+02:00",
                        },
                        "labels": {"step_label": "normal"},
                    }
                    for record in records
                ],
                "dependencies": [],
            },
            "execution_schedule": {
                "mode": "waves",
                "max_parallel_actor_sessions": max(1, len(actor_sessions)),
                "waves": [
                    {
                        "wave_id": "W001",
                        "sequence_no": 1,
                        "planned_steps": [
                            {"planned_step_id": record.planned_step_id, "startup_order": index}
                            for index, record in enumerate(records, start=1)
                        ],
                    }
                ],
            },
            "validation_report": {"errors": [], "warnings": []},
        }
    )


def _trace_from_records_with_waves(records: list[ExecutionTaskRecord], waves: list[list[str]]) -> CanonicalTrace:
    payload = _trace_from_records(records).model_dump(mode="json")
    payload["execution_schedule"]["waves"] = [
        {
            "wave_id": f"W{index:03d}",
            "sequence_no": index,
            "planned_steps": [
                {"planned_step_id": planned_step_id, "startup_order": startup_order}
                for startup_order, planned_step_id in enumerate(planned_step_ids, start=1)
            ],
        }
        for index, planned_step_ids in enumerate(waves, start=1)
    ]
    return CanonicalTrace.model_validate(payload)


def _init_from_records(
    records: list[ExecutionTaskRecord],
    *,
    login_url: str | None = None,
    selectors: dict[str, str] | None = None,
    include_password: bool = True,
) -> SessionInitRecord:
    seen: dict[str, ExecutionTaskRecord] = {}
    for record in records:
        seen.setdefault(record.actor_session_id, record)

    return SessionInitRecord(
        line_number=1,
        users=[
            SessionInitUser(
                actor_session_id=record.actor_session_id,
                synthetic_actor_id=record.synthetic_actor_id,
                username=record.synthetic_actor_id,
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
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
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
            planned_step_id=context.planned_step_id,
            actor_session_id=context.actor_session_id,
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
            planned_step_id=context.planned_step_id,
            actor_session_id=context.actor_session_id,
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
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    return registry


def _home_reset_failure_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        context.get_browser_session().fiori_messages.append(
            {
                "severity": "error",
                "text": "SAP says no",
                "source": "sap-message-popover",
                "url": "https://sap.example.test/current",
            }
        )
        raise RuntimeError("planned tool failure")

    def run_ok(context, _params: NoInput) -> ToolResult:
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fail_tool", input_model=NoInput, run=run_tool))
    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_ok))
    return registry


def _interrupt_registry() -> ToolRegistry:
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        raise KeyboardInterrupt

    registry.register(ToolSpec(name="fiori.interrupt_tool", input_model=NoInput, run=run_tool))
    return registry


def _noop_registry(calls: list[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        if calls is not None:
            calls.append(context.record.planned_step_id)
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="test.noop", input_model=NoInput, run=run_tool))
    return registry


def _parallel_probe_registry(events: list[tuple[str, str]], barrier: threading.Barrier) -> ToolRegistry:
    registry = ToolRegistry()
    lock = threading.Lock()

    def run_tool(context, _params: NoInput) -> ToolResult:
        planned_step_id = context.record.planned_step_id
        with lock:
            events.append(("started", planned_step_id))
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError as exc:
            raise AssertionError("wave planned steps did not overlap") from exc
        with lock:
            events.append(("finished", planned_step_id))
        return ToolResult(
            planned_step_id=planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="test.parallel_probe", input_model=NoInput, run=run_tool))
    return registry


def _parallel_failure_registry(events: list[tuple[str, str]], barrier: threading.Barrier) -> ToolRegistry:
    registry = ToolRegistry()
    lock = threading.Lock()

    def run_tool(context, _params: NoInput) -> ToolResult:
        planned_step_id = context.record.planned_step_id
        with lock:
            events.append(("started", planned_step_id))
        if planned_step_id in {"C001_A1", "C002_A1"}:
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError as exc:
                raise AssertionError("wave planned steps did not overlap") from exc
        if planned_step_id == "C001_A1":
            raise RuntimeError("planned failure")
        with lock:
            events.append(("finished", planned_step_id))
        return ToolResult(
            planned_step_id=planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="test.parallel_failure", input_model=NoInput, run=run_tool))
    return registry


class ThreadedFakeContext:
    def __init__(self, record: ExecutionTaskRecord, pool: ThreadPoolExecutor) -> None:
        self.record = record
        self.planned_step_id = record.planned_step_id
        self.actor_session_id = record.actor_session_id
        self.tool = record.tool
        self._pool = pool

    def submit_in_actor_session(self, operation):
        return self._pool.submit(operation, self)

    def run_in_actor_session(self, operation):
        return self.submit_in_actor_session(operation).result()


def test_executor_starts_actor_session_logins_in_parallel(tmp_path, monkeypatch):
    records = [
        _record(
            "C001_A1",
            tool="test.noop",
            synthetic_actor_id="buyer-a",
            actor_session_id="buyer-a-session",
            case_id="P2P_C001",
        ),
        _record(
            "C002_A1",
            tool="test.noop",
            synthetic_actor_id="buyer-b",
            actor_session_id="buyer-b-session",
            case_id="P2P_C002",
        ),
    ]
    trace = _trace_from_records(records)
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    barrier = threading.Barrier(2)
    events: list[tuple[str, str]] = []
    event_lock = threading.Lock()

    def parallel_login(context, params) -> ToolResult:
        with event_lock:
            events.append(("started", params.username))
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError as exc:
            raise AssertionError("all logins were not started before waiting for completion") from exc
        with event_lock:
            events.append(("finished", params.username))
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "current_url": f"https://sap.example.test/{params.username}"},
        )

    monkeypatch.setattr(executor_module, "run_login", parallel_login)

    with ThreadPoolExecutor(max_workers=2) as pool:
        TraceExecutor(registry=_noop_registry()).execute_canonical(
            trace,
            init=_init_from_records(records),
            context_factory=lambda record: ThreadedFakeContext(record, pool),
            evidence_writer=writer,
        )

    assert [event[0] for event in events[:2]] == ["started", "started"]


def test_executor_collects_parallel_login_failures_before_failing_run(tmp_path, monkeypatch):
    records = [
        _record(
            "C001_A1",
            tool="test.noop",
            synthetic_actor_id="buyer-a",
            actor_session_id="buyer-a-session",
            case_id="P2P_C001",
        ),
        _record(
            "C002_A1",
            tool="test.noop",
            synthetic_actor_id="buyer-b",
            actor_session_id="buyer-b-session",
            case_id="P2P_C002",
        ),
    ]
    trace = _trace_from_records(records)
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    started: list[str] = []
    planned_step_calls: list[str] = []

    def mixed_login(context, params) -> ToolResult:
        started.append(params.username)
        if params.username == "buyer-a":
            raise RuntimeError("bad login")
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "current_url": "https://sap.example.test/home"},
        )

    monkeypatch.setattr(executor_module, "run_login", mixed_login)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with pytest.raises(RuntimeError, match="bad login"):
            TraceExecutor(registry=_noop_registry(planned_step_calls)).execute_canonical(
                trace,
                init=_init_from_records(records),
                context_factory=lambda record: ThreadedFakeContext(record, pool),
                evidence_writer=writer,
            )

    assert sorted(started) == ["buyer-a", "buyer-b"]
    assert planned_step_calls == []
    events = _read_events(tmp_path)
    assert [event["event_type"] for event in events if event["event_type"] == "login_started"] == [
        "login_started",
        "login_started",
    ]
    assert any(event["event_type"] == "login_failed" and event["actor_session_id"] == "buyer-a-session" for event in events)
    assert any(event["event_type"] == "login_succeeded" and event["actor_session_id"] == "buyer-b-session" for event in events)
    assert any(event["event_type"] == "run_failed" and "buyer-a-session" in event["error"] for event in events)


def test_executor_runs_wave_planned_steps_in_parallel(tmp_path, monkeypatch):
    records = [
        _record(
            "C001_A1",
            tool="test.parallel_probe",
            synthetic_actor_id="buyer-a",
            actor_session_id="buyer-a-session",
            case_id="P2P_C001",
        ),
        _record(
            "C002_A1",
            tool="test.parallel_probe",
            synthetic_actor_id="buyer-b",
            actor_session_id="buyer-b-session",
            case_id="P2P_C002",
        ),
    ]
    events: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = _run_canonical_records(
            executor=TraceExecutor(registry=_parallel_probe_registry(events, barrier)),
            records=records,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            context_factory=lambda record: ThreadedFakeContext(record, pool),
        )

    assert [event[0] for event in events[:2]] == ["started", "started"]
    assert [result.planned_step_id for result in results if not result.planned_step_id.startswith("init-login")] == [
        "C001_A1",
        "C002_A1",
    ]


def test_parallel_wave_failure_skips_only_failed_case_later_steps(tmp_path, monkeypatch):
    records = [
        _record(
            "C001_A1",
            tool="test.parallel_failure",
            synthetic_actor_id="buyer-a",
            actor_session_id="buyer-a-session",
            case_id="P2P_C001",
        ),
        _record(
            "C002_A1",
            tool="test.parallel_failure",
            synthetic_actor_id="buyer-b",
            actor_session_id="buyer-b-session",
            case_id="P2P_C002",
        ),
        _record(
            "C001_A2",
            tool="test.parallel_failure",
            synthetic_actor_id="buyer-a",
            actor_session_id="buyer-a-session",
            case_id="P2P_C001",
        ),
        _record(
            "C002_A2",
            tool="test.parallel_failure",
            synthetic_actor_id="buyer-b",
            actor_session_id="buyer-b-session",
            case_id="P2P_C002",
        ),
    ]
    trace = _trace_from_records_with_waves(records, [["C001_A1", "C002_A1"], ["C001_A2", "C002_A2"]])
    events: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)
    monkeypatch.setattr(executor_module, "run_login", _fake_login)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = TraceExecutor(registry=_parallel_failure_registry(events, barrier)).execute_canonical(
            trace,
            init=_init_from_records(records),
            context_factory=lambda record: ThreadedFakeContext(record, pool),
            evidence_writer=ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id),
        )

    assert [event[0] for event in events[:2]] == ["started", "started"]
    assert [result.planned_step_id for result in results if not result.planned_step_id.startswith("init-login")] == [
        "C002_A1",
        "C002_A2",
    ]
    assert ("started", "C001_A2") not in events
    assert ("started", "C002_A2") in events
    event_types_by_step = [
        (event["event_type"], event.get("planned_step_id"))
        for event in _read_events(tmp_path)
        if event.get("planned_step_id") in {"C001_A1", "C001_A2", "C002_A1", "C002_A2"}
    ]
    assert ("planned_step_failed", "C001_A1") in event_types_by_step
    assert ("planned_step_skipped", "C001_A2") in event_types_by_step
    assert ("planned_step_succeeded", "C002_A2") in event_types_by_step


def test_executor_logs_unknown_tools_as_planned_step_failure(tmp_path, monkeypatch):
    contexts: list[ExecutionTaskRecord] = []
    record = _record("planned-step-1", tool="missing.tool")

    results = _run_canonical_records(
        executor=TraceExecutor(),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: contexts.append(item) or SimpleNamespace(record=item, **item.model_dump()),
    )

    assert [result.planned_step_id for result in results] == ["init-login-buyer-session"]
    assert [item.planned_step_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "planned_step_failed" and "missing.tool" in event["error"] for event in events)


def test_executor_logs_tool_input_validation_errors(tmp_path, monkeypatch):
    contexts: list[ExecutionTaskRecord] = []
    record = _record(
        "planned-step-1",
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

    assert [item.planned_step_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "planned_step_failed" and "Invalid input" in event["error"] for event in events)


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

    trace = _trace_from_records_with_waves(records, [["C042_A1"], ["C042_A2"]])
    monkeypatch.setattr(executor_module, "run_login", _fake_login)

    results = TraceExecutor(registry=_state_test_registry(captured_inputs)).execute_canonical(
        trace,
        init=_init_from_records(records),
        context_factory=lambda record: SimpleNamespace(record=record, **record.model_dump()),
        evidence_writer=ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id),
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

    assert [item.planned_step_id for item in contexts] == ["init-login-buyer-session"]
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "planned_step_failed" and "C042_A2" in event["error"] for event in events)


def test_executor_clicks_sap_home_logo_twice_after_successful_fiori_tool(tmp_path, monkeypatch):
    page = FakeHomeResetPage(logo_click_succeeds=True)
    record = _record("planned-step-1", tool="fiori.fake_tool")

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
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, "status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    record = _record("planned-step-1", tool="fiori.fake_tool")
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


def test_executor_logs_fiori_messages_once_per_case_actor(caplog):
    executor = TraceExecutor()
    record = _record("C001_A1", tool="fiori.fake_tool", case_id="P2P_C001")
    other_actor_record = _record(
        "C001_A2",
        tool="fiori.fake_tool",
        synthetic_actor_id="buyer-b",
        actor_session_id="buyer-b-session",
        case_id="P2P_C001",
    )
    session = SimpleNamespace(fiori_messages=[])
    other_actor_session = SimpleNamespace(fiori_messages=[])
    sap_message = {
        "severity": "error",
        "text": "Geben Sie ein Rechnungsdatum ein.",
        "source": "sap-message-popover",
        "url": "https://sap.example.test/invoice",
    }

    with caplog.at_level(logging.INFO, logger="erp_trace_executor.executor"):
        executor.fiori_message_sink_for(record, session).append(sap_message)
        executor.fiori_message_sink_for(record, session).append(
            {**sap_message, "url": "https://sap.example.test/other"}
        )
        executor.fiori_message_sink_for(other_actor_record, other_actor_session).append(sap_message)

    messages = [item.getMessage() for item in caplog.records if item.name == "erp_trace_executor.executor"]
    assert messages == [
        "SAP Fiori error message for case P2P_C001, actor session buyer-session, "
        "planned step C001_A1: Geben Sie ein Rechnungsdatum ein.",
        "SAP Fiori error message for case P2P_C001, actor session buyer-b-session, "
        "planned step C001_A2: Geben Sie ein Rechnungsdatum ein.",
    ]
    assert len(session.fiori_messages) == 2
    assert len(other_actor_session.fiori_messages) == 1


def test_execution_contexts_use_configured_fiori_message_sink_factory():
    record = _record("C001_A1", tool="fiori.fake_tool", case_id="P2P_C001")
    session = SimpleNamespace(page=object(), fiori_messages=[])
    sink: list[dict[str, str]] = []
    calls: list[tuple[ExecutionTaskRecord, object]] = []

    class FakeSessionManager:
        def get_session(self, *, actor_session_id: str, synthetic_actor_id: str):
            return session

    def sink_factory(received_record, received_session):
        calls.append((received_record, received_session))
        return sink

    execution_context = ExecutionContext(
        record=record,
        session_manager=FakeSessionManager(),
        fiori_message_sink_factory=sink_factory,
    )
    actor_context = ActorSessionExecutionContext(
        record=record,
        session=session,
        fiori_message_sink_factory=sink_factory,
    )

    assert execution_context.get_fiori_page()._message_handler._message_sink is sink
    assert actor_context.get_fiori_page()._message_handler._message_sink is sink
    assert calls == [(record, session), (record, session)]


def test_execution_context_runtime_delay_marker_uses_multiplier_and_cap():
    record = _record("C001_A1", tool="test.noop")
    record.meta["human_delay_profile"] = {
        "delay_multiplier": 2.0,
        "runtime_delay_cap_seconds": 2.5,
    }
    page = SimpleNamespace(waited=[])

    def wait_for_timeout(timeout_ms):
        page.waited.append(timeout_ms)

    page.wait_for_timeout = wait_for_timeout
    session = SimpleNamespace(page=page, fiori_messages=[])

    class FakeSessionManager:
        def get_session(self, *, actor_session_id: str, synthetic_actor_id: str):
            return session

    context = ExecutionContext(record=record, session_manager=FakeSessionManager())

    context.runtime_delay_marker("review_save", 1.5)

    assert page.waited == [2500]


def test_execution_context_runtime_delay_marker_skips_invalid_profile(caplog):
    record = _record("C001_A1", tool="test.noop")
    record.meta["human_delay_profile"] = {
        "delay_multiplier": 0,
        "runtime_delay_cap_seconds": 2.5,
    }

    class FakeSessionManager:
        def get_session(self, *, actor_session_id: str, synthetic_actor_id: str):
            raise AssertionError("invalid delay profile should not open a browser session")

    context = ExecutionContext(record=record, session_manager=FakeSessionManager())

    caplog.set_level(logging.WARNING, logger="erp_trace_executor.context")
    context.runtime_delay_marker("review_save", 1.5)

    assert "invalid human_delay_profile metadata" in caplog.text
    assert "C001_A1" in caplog.text
    assert "buyer-session" in caplog.text


def test_executor_falls_back_to_current_login_url_when_home_logo_clicks_fail(tmp_path, monkeypatch):
    page = FakeHomeResetPage(logo_click_succeeds=False)
    record = _record("planned-step-1", tool="fiori.fake_tool")

    _run_canonical_records(
        executor=TraceExecutor(registry=_home_reset_registry()),
        records=[record],
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_factory=lambda item: FakeHomeResetContext(item, page),
    )

    assert page.logo_click_count == 2
    assert page.goto_urls == ["https://sap.example.test/home"]


def test_executor_logs_failed_fiori_planned_step_resets_home_and_continues_other_cases(
    tmp_path, monkeypatch, caplog
):
    page = FakeHomeResetPage(logo_click_succeeds=True)
    records = [
        _record("C001_A1", tool="fiori.fail_tool", case_id="P2P_C001"),
        _record("C002_A1", tool="fiori.fake_tool", case_id="P2P_C002"),
    ]

    with caplog.at_level(logging.ERROR, logger="erp_trace_executor.evidence"):
        results = _run_canonical_records(
            executor=TraceExecutor(registry=_home_reset_failure_registry()),
            records=records,
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            context_factory=lambda item: FakeHomeResetContext(item, page),
        )

    assert [result.planned_step_id for result in results] == ["init-login-buyer-session", "C002_A1"]
    assert page.logo_click_count == 4
    assert "Failed planned step C001_A1" in caplog.text
    assert "planned tool failure" in caplog.text
    assert "SAP says no" in caplog.text
    events = _read_events(tmp_path)
    failed = next(event for event in events if event["event_type"] == "planned_step_failed")
    assert failed["planned_step_id"] == "C001_A1"
    assert failed["sap_messages"][0]["text"] == "SAP says no"
    assert any(event["event_type"] == "planned_step_succeeded" and event["planned_step_id"] == "C002_A1" for event in events)


def test_executor_fails_run_when_home_reset_after_planned_step_failure_fails(tmp_path, monkeypatch, caplog):
    page = FakeHomeResetPage(logo_click_succeeds=False, goto_raises=True)
    record = _record("C001_A1", tool="fiori.fail_tool", case_id="P2P_C001")

    with caplog.at_level(logging.ERROR, logger="erp_trace_executor.evidence"):
        with pytest.raises(PlaywrightError, match="cannot goto home"):
            _run_canonical_records(
                executor=TraceExecutor(registry=_home_reset_failure_registry()),
                records=[record],
                tmp_path=tmp_path,
                monkeypatch=monkeypatch,
                context_factory=lambda item: FakeHomeResetContext(item, page),
            )

    assert "Home reset failed for planned step C001_A1" in caplog.text
    events = _read_events(tmp_path)
    assert any(event["event_type"] == "home_reset_failed" for event in events)
    assert any(event["event_type"] == "run_failed" for event in events)


def test_executor_logs_planned_step_and_run_interrupted_before_reraising(tmp_path, monkeypatch):
    record = _record("C001_A1", tool="fiori.interrupt_tool", case_id="P2P_C001")

    with pytest.raises(KeyboardInterrupt):
        _run_canonical_records(
            executor=TraceExecutor(registry=_interrupt_registry()),
            records=[record],
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            context_factory=lambda item: FakeHomeResetContext(item, FakeHomeResetPage(logo_click_succeeds=True)),
        )

    events = _read_events(tmp_path)
    assert [event["event_type"] for event in events if event["event_type"].endswith("_interrupted")] == [
        "planned_step_interrupted",
        "run_interrupted",
    ]
    planned_step_event = next(event for event in events if event["event_type"] == "planned_step_interrupted")
    assert planned_step_event["planned_step_id"] == "C001_A1"
    assert planned_step_event["severity"] == "WARNING"
    run_event = next(event for event in events if event["event_type"] == "run_interrupted")
    assert run_event["planned_step_id"] == "C001_A1"
    assert run_event["severity"] == "WARNING"


def test_executor_logs_login_and_run_interrupted_before_reraising(tmp_path, monkeypatch):
    record = _record("C001_A1", tool="fiori.fake_tool", case_id="P2P_C001")
    trace = _trace_from_records([record])
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)

    def interrupt_login(_context, _params) -> ToolResult:
        raise KeyboardInterrupt

    monkeypatch.setattr(executor_module, "run_login", interrupt_login)

    with pytest.raises(KeyboardInterrupt):
        TraceExecutor(registry=_home_reset_registry()).execute_canonical(
            trace,
            init=_init_from_records([record]),
            context_factory=lambda item: FakeHomeResetContext(item, FakeHomeResetPage(logo_click_succeeds=True)),
            evidence_writer=writer,
        )

    events = _read_events(tmp_path)
    assert [event["event_type"] for event in events if event["event_type"].endswith("_interrupted")] == [
        "login_interrupted",
        "run_interrupted",
    ]
    login_event = next(event for event in events if event["event_type"] == "login_interrupted")
    assert login_event["actor_session_id"] == "buyer-session"
    assert login_event["severity"] == "WARNING"
    run_event = next(event for event in events if event["event_type"] == "run_interrupted")
    assert run_event["actor_session_id"] == "buyer-session"
    assert run_event["severity"] == "WARNING"


class FakeHomeResetContext:
    def __init__(self, record: ExecutionTaskRecord, page: "FakeHomeResetPage") -> None:
        self.record = record
        self.planned_step_id = record.planned_step_id
        self.actor_session_id = record.actor_session_id
        self.tool = record.tool
        self._session = SimpleNamespace(page=page, fiori_messages=[])

    def get_browser_session(self):
        return self._session


class FakeMessageContext:
    def __init__(self, record: ExecutionTaskRecord) -> None:
        self.record = record
        self.planned_step_id = record.planned_step_id
        self.actor_session_id = record.actor_session_id
        self.tool = record.tool
        self.session = SimpleNamespace(
            page=FakeHomeResetPage(logo_click_succeeds=True),
            fiori_messages=[],
        )

    def get_browser_session(self):
        return self.session


class FakeHomeResetPage:
    url = "https://sap.example.test/current"

    def __init__(self, *, logo_click_succeeds: bool, goto_raises: bool = False) -> None:
        self.logo_click_succeeds = logo_click_succeeds
        self.goto_raises = goto_raises
        self.logo_click_count = 0
        self.goto_urls: list[str] = []

    def goto(self, url: str) -> None:
        if self.goto_raises:
            raise PlaywrightError("cannot goto home")
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
        first = session_manager.run_for_session(
            actor_session_id="session-1",
            synthetic_actor_id="user-1",
            operation=id,
        )
        second = session_manager.run_for_session(
            actor_session_id="session-1",
            synthetic_actor_id="user-1",
            operation=id,
        )
        other = session_manager.run_for_session(
            actor_session_id="session-2",
            synthetic_actor_id="user-1",
            operation=id,
        )

        assert first == second
        assert other != first
        assert session_manager.active_session_count() == 2


def test_browser_session_manager_serializes_same_actor_session_operations():
    events: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def first_operation(_session):
        events.append("first-started")
        first_started.set()
        assert release_first.wait(timeout=1)
        events.append("first-finished")

    def second_operation(_session):
        events.append("second-started")
        second_started.set()
        events.append("second-finished")

    with BrowserSessionManager() as session_manager:
        first = session_manager.submit_for_session(
            actor_session_id="session-1",
            synthetic_actor_id="user-1",
            operation=first_operation,
        )
        assert first_started.wait(timeout=2)
        second = session_manager.submit_for_session(
            actor_session_id="session-1",
            synthetic_actor_id="user-1",
            operation=second_operation,
        )

        assert not second_started.wait(timeout=0.1)
        release_first.set()
        first.result()
        second.result()

    assert events == ["first-started", "first-finished", "second-started", "second-finished"]


def test_browser_session_manager_rejects_mixed_users_for_same_session():
    with BrowserSessionManager() as session_manager:
        session_manager.run_for_session(
            actor_session_id="session-1",
            synthetic_actor_id="user-1",
            operation=id,
        )

        with pytest.raises(SessionUserMismatchError, match="session-1"):
            session_manager.run_for_session(
                actor_session_id="session-1",
                synthetic_actor_id="user-2",
                operation=id,
            )


def test_browser_session_manager_closes_partial_resources_when_initialization_fails(monkeypatch):
    events: list[str] = []

    class FakePlaywrightBootstrap:
        def start(self):
            events.append("playwright_start")
            return FakePlaywright()

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = FakeChromium()

        def stop(self) -> None:
            events.append("playwright_stop")

    class FakeChromium:
        def launch(self, *, headless: bool):
            events.append(f"browser_launch_{headless}")
            return FakeBrowser()

    class FakeBrowser:
        def new_context(self):
            events.append("context_open")
            return FakeBrowserContext()

        def close(self) -> None:
            events.append("browser_close")

    class FakeBrowserContext:
        def new_page(self):
            raise RuntimeError("cannot open page")

        def close(self) -> None:
            events.append("context_close")

    monkeypatch.setattr(session_module, "sync_playwright", lambda: FakePlaywrightBootstrap())

    with BrowserSessionManager() as session_manager:
        with pytest.raises(RuntimeError, match="cannot open page"):
            session_manager.run_for_session(
                actor_session_id="session-1",
                synthetic_actor_id="user-1",
                operation=id,
            )

    assert events == [
        "playwright_start",
        "browser_launch_True",
        "context_open",
        "context_close",
        "browser_close",
        "playwright_stop",
    ]


def test_executor_runs_login_then_purchase_requisition_against_fixture_app(fixture_app_url, tmp_path):
    records = [
        _record(
            "planned-step-1",
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
            "planned-step-1",
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
            "planned-step-1",
            tool="fiori.create_purchase_requisition",
            input=_purchase_requisition_input(),
            required_sap_object_keys=["purchase_requisition.pr_number"],
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
