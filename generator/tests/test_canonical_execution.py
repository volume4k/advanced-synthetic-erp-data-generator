from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import BaseModel

from erp_trace_executor import executor as executor_module
from erp_trace_executor.canonical import CanonicalTrace, build_init_from_sessions, load_canonical_trace
from erp_trace_executor.evidence import ExecutionEvidenceWriter
from erp_trace_executor.errors import TraceParseError
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
            task_id=context.record.task_id,
            session_id=context.record.session_id,
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
            task_id=context.record.task_id,
            session_id=context.record.session_id,
            tool=context.record.tool,
            data={"success": True, **returned},
        )

    def run_consume(context, params: ConsumeInput) -> ToolResult:
        return ToolResult(
            task_id=context.record.task_id,
            session_id=context.record.session_id,
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
        "trace_version": "0.1",
        "run_id": "RUN_CANONICAL",
        "config_hash": "config",
        "tool_catalog_hash": "tools",
        "trace_generator_version": "0.1.0",
        "llm_metadata": {"used": False},
        "sessions": [
            {
                "session_id": "buyer-session",
                "virtual_actor_id": "buyer",
                "technical_user_id": "TU_01",
                "username_env_var": "SAP_USER_1_UN",
                "password_env_var": "SAP_USER_1_PW",
                "login_url_env_var": "SAP_URL",
            }
        ],
        "cases": [
            {"case_id": "C001", "process_type": "procure_to_pay", "scenario_id": "NORMAL", "case_label": "normal", "line_items": []},
            {"case_id": "C002", "process_type": "procure_to_pay", "scenario_id": "NORMAL", "case_label": "normal", "line_items": []},
        ],
        "dependency_graph": {
            "nodes": [
                _node("C001_A1", "C001", "test.produce", {"number": "PR-1"}, ["purchase_requisition.pr_number"]),
                _node("C002_A1", "C002", "test.produce", {"number": "FAIL"}, ["purchase_requisition.pr_number"]),
                _node("C001_A2", "C001", "test.consume", {"purchase_requisition": "$purchase_requisition.pr_number", "number": "PO-1"}, ["purchase_order.po_number"]),
                _node("C002_A2", "C002", "test.consume", {"purchase_requisition": "$purchase_requisition.pr_number", "number": "PO-2"}, ["purchase_order.po_number"]),
            ],
            "edges": [],
        },
        "execution_schedule": {
            "mode": "waves",
            "max_parallel_sessions": 2,
            "waves": [
                {"wave_id": "W001", "sequence_no": 1, "nodes": [{"node_id": "C001_A1", "startup_order": 1}, {"node_id": "C002_A1", "startup_order": 2}]},
                {"wave_id": "W002", "sequence_no": 2, "nodes": [{"node_id": "C001_A2", "startup_order": 1}, {"node_id": "C002_A2", "startup_order": 2}]},
            ],
        },
        "validation_report": {"errors": [], "warnings": []},
    }


def _node(node_id: str, case_id: str, tool: str, inputs: dict, expected_outputs: list[str]) -> dict:
    return {
        "node_id": node_id,
        "case_id": case_id,
        "step_type": "sample_step",
        "tool_name": tool,
        "virtual_actor_id": "buyer",
        "technical_sap_user": "TU_01",
        "session_id": "buyer-session",
        "inputs": inputs,
        "expected_outputs": expected_outputs,
        "business_dates": {},
        "target_synthetic_time": {"start": "2026-05-18T08:00:00+02:00", "end": "2026-05-18T08:05:00+02:00"},
        "labels": {"step_label": "normal"},
    }


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _context(record):
    return SimpleNamespace(
        record=record,
        task_id=record.task_id,
        session_id=record.session_id,
        tool=record.tool,
    )


def test_canonical_loader_rejects_wave_node_refs_without_nodes(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["execution_schedule"]["waves"][0]["nodes"][0]["node_id"] = "missing-node"
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match="unknown node"):
        load_canonical_trace(path)


def test_canonical_loader_rejects_edge_refs_without_nodes(tmp_path: Path) -> None:
    payload = _canonical_payload()
    payload["dependency_graph"]["edges"] = [
        {"from": "C001_A1", "to": "missing-node", "type": "data_dependency", "reason": "test"}
    ]
    path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(path, payload)

    with pytest.raises(TraceParseError, match="unknown to node"):
        load_canonical_trace(path)


def test_build_init_from_sessions_preserves_login_selectors() -> None:
    payload = _canonical_payload()
    payload["sessions"][0]["username_selector"] = "#user"
    payload["sessions"][0]["password_selector"] = "#pass"
    payload["sessions"][0]["submit_selector"] = "#submit"
    payload["sessions"][0]["success_selector"] = "#done"
    trace = CanonicalTrace.model_validate(payload)

    init = build_init_from_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )

    assert init.users[0].username_selector == "#user"
    assert init.users[0].password_selector == "#pass"
    assert init.users[0].submit_selector == "#submit"
    assert init.users[0].success_selector == "#done"


def test_evidence_writer_rejects_unsafe_run_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe filename"):
        ExecutionEvidenceWriter(tmp_path, run_id="../escape")


def test_canonical_executor_logs_registry_and_skips_failed_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trace_path = tmp_path / "trace.execution-trace.yaml"
    _write_yaml(trace_path, _canonical_payload())
    trace = load_canonical_trace(trace_path)
    init = build_init_from_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    monkeypatch.setattr(
        executor_module,
        "run_login",
        lambda context, params: ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
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

    assert [result.task_id for result in results] == [
        "init-login-buyer-session",
        "C001_A1",
        "C001_A2",
    ]
    events = _read_jsonl(tmp_path / "RUN_CANONICAL.execution-log.jsonl")
    assert [event["event_type"] for event in events if event["event_type"].startswith("node_")] == [
        "node_started",
        "node_succeeded",
        "node_started",
        "node_failed",
        "node_started",
        "node_succeeded",
        "node_skipped",
    ]
    assert any(event["event_type"] == "case_failed" and event["case_id"] == "C002" for event in events)
    registry_entries = _read_jsonl(tmp_path / "RUN_CANONICAL.object-registry.jsonl")
    assert registry_entries == [
        {
            "run_id": "RUN_CANONICAL",
            "case_id": "C001",
            "node_id": "C001_A1",
            "scenario_id": "NORMAL",
            "virtual_actor_id": "buyer",
            "technical_user_id": "TU_01",
            "tool": "test.produce",
            "object_type": "purchase_requisition",
            "keys": {"pr_number": "PR-1"},
            "parent_references": [],
            "status": "created",
        },
        {
            "run_id": "RUN_CANONICAL",
            "case_id": "C001",
            "node_id": "C001_A2",
            "scenario_id": "NORMAL",
            "virtual_actor_id": "buyer",
            "technical_user_id": "TU_01",
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
    payload["execution_schedule"]["waves"][0]["nodes"] = [payload["execution_schedule"]["waves"][0]["nodes"][0]]
    payload["dependency_graph"]["nodes"] = payload["dependency_graph"]["nodes"][:1]
    payload["cases"] = payload["cases"][:1]
    _write_yaml(trace_path, payload)
    trace = load_canonical_trace(trace_path)
    init = build_init_from_sessions(
        trace,
        {"SAP_USER_1_UN": "BUYER1", "SAP_USER_1_PW": "secret", "SAP_URL": "https://sap.example.test"},
    )
    writer = ExecutionEvidenceWriter(tmp_path, run_id=trace.run_id)
    monkeypatch.setattr(
        executor_module,
        "run_login",
        lambda context, params: ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
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
    assert any(event["event_type"] == "node_failed" and "purchase_requisition.pr_number" in event["error"] for event in events)
    assert not (tmp_path / "RUN_CANONICAL.object-registry.jsonl").exists()
