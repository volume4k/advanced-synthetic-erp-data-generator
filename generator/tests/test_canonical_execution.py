from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import BaseModel

from erp_trace_executor import executor as executor_module
from erp_trace_executor.canonical import CanonicalTrace, build_init_from_actor_sessions, load_canonical_trace
from erp_trace_executor.evidence import ExecutionEvidenceWriter
from erp_trace_executor.errors import TraceExecutorError, TraceParseError
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.models import ToolResult
from erp_trace_executor.registry import ToolRegistry
from erp_trace_executor.tooling import ToolSpec


class ProduceInput(BaseModel):
    number: str


class ConsumeInput(BaseModel):
    purchase_requisition: str
    number: str


class NoInput(BaseModel):
    pass


def _registry(*, missing_expected_output: bool = False) -> ToolRegistry:
    registry = ToolRegistry()

    def run_login(context, _params: NoInput) -> ToolResult:
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True},
        )

    def run_produce(context, params: ProduceInput) -> ToolResult:
        if params.number == "FAIL":
            raise RuntimeError("planned failure")
        returned = {}
        if not missing_expected_output:
            returned = {
                "returned_objects": [
                    {"object_type": "purchase_requisition", "keys": {"pr_number": params.number}}
                ]
            }
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={"success": True, **returned},
        )

    def run_consume(context, params: ConsumeInput) -> ToolResult:
        return ToolResult(
            planned_step_id=context.record.planned_step_id,
            actor_session_id=context.record.actor_session_id,
            tool=context.record.tool,
            data={
                "success": True,
                "returned_objects": [
                    {"object_type": "purchase_order", "keys": {"po_number": params.number}}
                ],
            },
        )

    registry.register(ToolSpec(name="fiori.login", input_model=NoInput, run=run_login))
    registry.register(ToolSpec(name="test.produce", input_model=ProduceInput, run=run_produce))
    registry.register(ToolSpec(name="test.consume", input_model=ConsumeInput, run=run_consume))
    return registry


def _canonical_payload() -> dict:
    return {
        "trace_version": "0.3",
        "run_id": "RUN_CANONICAL",
        "config_hash": "config",
        "tool_catalog_hash": "tools",
        "trace_generator_version": "0.1.0",
        "llm_metadata": {"used": False},
        "actor_sessions": [
            {
                "actor_session_id": "buyer-session",
                "synthetic_actor_id": "buyer",
                "technical_sap_user_id": "TU_01",
                "username_env_var": "SAP_USER_1_UN",
                "password_env_var": "SAP_USER_1_PW",
                "login_url_env_var": "SAP_URL",
            }
        ],
        "cases": [
            {"case_id": "C001", "process_type": "procure_to_pay", "case_scenario_type": "NORMAL", "line_items": []},
            {"case_id": "C002", "process_type": "procure_to_pay", "case_scenario_type": "NORMAL", "line_items": []},
        ],
        "dependency_graph": {
            "planned_steps": [
                _node("C001_A1", "C001", "test.produce", {"number": "PR-1"}, ["purchase_requisition.pr_number"]),
                _node("C002_A1", "C002", "test.produce", {"number": "FAIL"}, ["purchase_requisition.pr_number"]),
                _node("C001_A2", "C001", "test.consume", {"purchase_requisition": "$purchase_requisition.pr_number", "number": "PO-1"}, ["purchase_order.po_number"]),
                _node("C002_A2", "C002", "test.consume", {"purchase_requisition": "$purchase_requisition.pr_number", "number": "PO-2"}, ["purchase_order.po_number"]),
            ],
            "dependencies": [],
        },
        "execution_schedule": {
            "mode": "waves",
            "max_parallel_actor_sessions": 2,
            "waves": [
                {"wave_id": "W001", "sequence_no": 1, "planned_steps": [{"planned_step_id": "C001_A1", "startup_order": 1}, {"planned_step_id": "C002_A1", "startup_order": 2}]},
                {"wave_id": "W002", "sequence_no": 2, "planned_steps": [{"planned_step_id": "C001_A2", "startup_order": 1}, {"planned_step_id": "C002_A2", "startup_order": 2}]},
            ],
        },
        "validation_report": {"errors": [], "warnings": []},
    }


def _node(planned_step_id: str, case_id: str, tool: str, inputs: dict, required_sap_object_keys: list[str]) -> dict:
    return {
        "planned_step_id": planned_step_id,
        "case_id": case_id,
        "step_type": "sample_step",
        "tool_name": tool,
        "synthetic_actor_id": "buyer",
        "technical_sap_user_id": "TU_01",
        "actor_session_id": "buyer-session",
        "inputs": inputs,
        "required_sap_object_keys": required_sap_object_keys,
        "planned_date_inputs": {},
        "planned_synthetic_time": {"start": "2026-05-18T08:00:00+02:00", "end": "2026-05-18T08:05:00+02:00"},
        "labels": {"step_label": "normal"},
    }


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _context(record):
    return SimpleNamespace(
        record=record,
        planned_step_id=record.planned_step_id,
        actor_session_id=record.actor_session_id,
        tool=record.tool,
    )


def test_canonical_loader_rejects_v01_traces(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["trace_version"] = "0.1"
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match="expected '0.3'"):
        load_canonical_trace(path)


def test_canonical_loader_accepts_requested_delivery_date_on_cases(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["cases"][0]["requested_delivery_date"] = "2026-06-01"
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    trace = load_canonical_trace(path)

    assert trace.cases[0].requested_delivery_date == "2026-06-01"


def test_canonical_loader_rejects_wave_planned_step_refs_without_planned_steps(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["execution_schedule"]["waves"][0]["planned_steps"][0]["planned_step_id"] = "missing-node"
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match="unknown planned step"):
        load_canonical_trace(path)


def test_canonical_loader_rejects_dependency_refs_without_planned_steps(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["dependency_graph"]["dependencies"] = [
        {"from_planned_step_id": "C001_A1", "to_planned_step_id": "missing-node", "type": "data_dependency", "reason": "test"}
    ]
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match="unknown to planned step"):
        load_canonical_trace(path)


def test_build_init_from_sessions_preserves_login_selectors() -> None:
    payload = _canonical_payload()
    payload["actor_sessions"][0]["username_selector"] = "#user"
    payload["actor_sessions"][0]["password_selector"] = "#pass"
    payload["actor_sessions"][0]["submit_selector"] = "#submit"
    payload["actor_sessions"][0]["success_selector"] = "#done"
    trace = CanonicalTrace.model_validate(payload)

    init = build_init_from_actor_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )

    assert init.users[0].username_selector == "#user"
    assert init.users[0].password_selector == "#pass"
    assert init.users[0].submit_selector == "#submit"
    assert init.users[0].success_selector == "#done"


def test_build_init_from_sessions_preserves_human_delay_profile() -> None:
    payload = _canonical_payload()
    payload["actor_sessions"][0]["human_delay_profile"] = {
        "delay_multiplier": 2.0,
    }
    trace = CanonicalTrace.model_validate(payload)

    init = build_init_from_actor_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )

    assert init.users[0].human_delay_profile.delay_multiplier == 2.0


@pytest.mark.parametrize(
    ("missing_key", "match"),
    [
        ("SAP_USER_1_PW", "missing password env var"),
        ("SAP_URL", "missing login URL env var"),
    ],
)
def test_build_init_from_sessions_rejects_missing_required_env_values(missing_key: str, match: str) -> None:
    trace = CanonicalTrace.model_validate(_canonical_payload())
    env_values = {
        "SAP_USER_1_UN": "BUYER1",
        "SAP_USER_1_PW": "secret",
        "SAP_URL": "https://sap.example.test",
    }
    env_values.pop(missing_key)

    with pytest.raises(TraceParseError, match=match):
        build_init_from_actor_sessions(trace, env_values)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("synthetic_actor_id", "other_actor", "synthetic_actor_id"),
        ("technical_sap_user_id", "TU_99", "technical_sap_user_id"),
    ],
)
def test_canonical_loader_rejects_planned_step_identity_mismatch(
    tmp_path: Path, field: str, value: str, match: str
) -> None:
    payload = _canonical_payload()
    payload["dependency_graph"]["planned_steps"][0][field] = value
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match=match):
        load_canonical_trace(path)


def test_evidence_writer_rejects_unsafe_run_ids(tmp_path: Path) -> None:
    for run_id in ["../escape", "..", ".", "/tmp/escape", r"nested\escape"]:
        with pytest.raises(ValueError, match="unsafe filename"):
            ExecutionEvidenceWriter(tmp_path, run_id=run_id)


def test_evidence_writer_logs_payload_metadata_without_values(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = ExecutionEvidenceWriter(tmp_path, run_id="safe-run")
    writer.execution_log_path.mkdir()

    with caplog.at_level(logging.ERROR, logger="erp_trace_executor.evidence"):
        with pytest.raises(TraceExecutorError):
            writer.log_event("planned_step_failed", password="secret-value")

    assert "secret-value" not in caplog.text
    assert "payload keys=" in caplog.text
    assert "event_type" in caplog.text


def test_evidence_writer_adds_message_and_severity_and_mirrors_to_logger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = ExecutionEvidenceWriter(tmp_path, run_id="RUN_TEST")

    with caplog.at_level(logging.DEBUG, logger="erp_trace_executor.evidence"):
        writer.log_event("run_started")
        writer.log_event("state_updated", planned_step_id="C001_A1", object_count=1)
        writer.log_event("planned_step_skipped", planned_step_id="C001_A2", reason="case_failed")
        writer.log_event("planned_step_failed", planned_step_id="C001_A3", error="tool exploded")

    events = _read_jsonl(tmp_path / "RUN_TEST.execution-log.jsonl")
    assert [(event["event_type"], event["severity"]) for event in events] == [
        ("run_started", "INFO"),
        ("state_updated", "DEBUG"),
        ("planned_step_skipped", "WARNING"),
        ("planned_step_failed", "ERROR"),
    ]
    assert events[0]["message"] == "Executor run started"
    assert events[1]["message"] == "State updated for planned step C001_A1"
    assert events[2]["message"] == "Skipped planned step C001_A2: case_failed"
    assert events[3]["message"] == "Failed planned step C001_A3: tool exploded"
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        ("INFO", "Executor run started"),
        ("DEBUG", "State updated for planned step C001_A1"),
        ("WARNING", "Skipped planned step C001_A2: case_failed"),
        ("ERROR", "Failed planned step C001_A3: tool exploded"),
    ]


def test_canonical_executor_logs_registry_and_skips_failed_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(trace_path, _canonical_payload())
    trace = load_canonical_trace(trace_path)
    init = build_init_from_actor_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    monkeypatch.setattr(
        executor_module,
        "run_login",
        lambda context, params: ToolResult(
            planned_step_id=context.planned_step_id,
            actor_session_id=context.actor_session_id,
            tool=context.tool,
            data={"success": True, "username": params.username},
        ),
    )

    results = TraceExecutor(registry=_registry()).execute_canonical(
        trace,
        init=init,
        context_factory=_context,
        evidence_writer=writer,
    )

    assert [result.planned_step_id for result in results] == [
        "init-login-buyer-session",
        "C001_A1",
        "C001_A2",
    ]
    events = _read_jsonl(tmp_path / "RUN_CANONICAL.execution-log.jsonl")
    assert [
        (event["event_type"], event.get("planned_step_id"))
        for event in events
        if event["event_type"].startswith("planned_step_")
    ] == [
        ("planned_step_started", "C001_A1"),
        ("planned_step_started", "C002_A1"),
        ("planned_step_succeeded", "C001_A1"),
        ("planned_step_failed", "C002_A1"),
        ("planned_step_started", "C001_A2"),
        ("planned_step_skipped", "C002_A2"),
        ("planned_step_succeeded", "C001_A2"),
    ]
    assert any(event["event_type"] == "case_failed" and event["case_id"] == "C002" for event in events)
    registry_entries = _read_jsonl(tmp_path / "RUN_CANONICAL.object-registry.jsonl")
    assert registry_entries == [
        {
            "run_id": "RUN_CANONICAL",
            "case_id": "C001",
            "planned_step_id": "C001_A1",
            "actor_session_id": "buyer-session",
            "case_scenario_type": "NORMAL",
            "synthetic_actor_id": "buyer",
            "technical_sap_user_id": "TU_01",
            "tool": "test.produce",
            "object_type": "purchase_requisition",
            "keys": {"pr_number": "PR-1"},
            "parent_references": [],
            "status": "created",
        },
        {
            "run_id": "RUN_CANONICAL",
            "case_id": "C001",
            "planned_step_id": "C001_A2",
            "actor_session_id": "buyer-session",
            "case_scenario_type": "NORMAL",
            "synthetic_actor_id": "buyer",
            "technical_sap_user_id": "TU_01",
            "tool": "test.consume",
            "object_type": "purchase_order",
            "keys": {"po_number": "PO-1"},
            "parent_references": [
                {"object_type": "purchase_requisition", "key": "pr_number", "value": "PR-1"}
            ],
            "status": "created",
        },
    ]


def test_canonical_executor_marks_missing_expected_output_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_path = tmp_path / "trace.execution-trace.yaml"
    payload = _canonical_payload()
    payload["execution_schedule"]["waves"] = [payload["execution_schedule"]["waves"][0]]
    payload["execution_schedule"]["waves"][0]["planned_steps"] = [payload["execution_schedule"]["waves"][0]["planned_steps"][0]]
    payload["dependency_graph"]["planned_steps"] = payload["dependency_graph"]["planned_steps"][:1]
    payload["cases"] = payload["cases"][:1]
    _write_yaml(trace_path, payload)
    trace = load_canonical_trace(trace_path)
    init = build_init_from_actor_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    monkeypatch.setattr(
        executor_module,
        "run_login",
        lambda context, params: ToolResult(
            planned_step_id=context.planned_step_id,
            actor_session_id=context.actor_session_id,
            tool=context.tool,
            data={"success": True, "username": params.username},
        ),
    )

    TraceExecutor(registry=_registry(missing_expected_output=True)).execute_canonical(
        trace,
        init=init,
        context_factory=_context,
        evidence_writer=writer,
    )

    events = _read_jsonl(tmp_path / "RUN_CANONICAL.execution-log.jsonl")
    assert any(event["event_type"] == "planned_step_failed" and "purchase_requisition.pr_number" in event["error"] for event in events)
    assert not (tmp_path / "RUN_CANONICAL.object-registry.jsonl").exists()
