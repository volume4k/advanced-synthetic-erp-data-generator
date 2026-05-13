"""Canonical execution-trace loading for wave-aware execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.models import SessionInitRecord, SessionInitUser


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalSession(CanonicalModel):
    session_id: str
    virtual_actor_id: str
    technical_user_id: str
    username_env_var: str
    password_env_var: str
    login_url_env_var: str
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_selector: str | None = None


class CanonicalCase(CanonicalModel):
    case_id: str
    process_type: str
    scenario_id: str
    case_label: str
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalTargetTime(CanonicalModel):
    start: str
    end: str


class CanonicalNode(CanonicalModel):
    node_id: str
    case_id: str
    step_type: str
    tool_name: str
    virtual_actor_id: str
    technical_sap_user: str
    session_id: str
    inputs: dict[str, Any]
    expected_outputs: list[str]
    business_dates: dict[str, str]
    target_synthetic_time: CanonicalTargetTime
    labels: dict[str, str] = Field(default_factory=dict)


class CanonicalEdge(CanonicalModel):
    from_: str = Field(alias="from")
    to: str
    type: str
    reason: str


class CanonicalDependencyGraph(CanonicalModel):
    nodes: list[CanonicalNode]
    edges: list[CanonicalEdge]


class CanonicalScheduledNode(CanonicalModel):
    node_id: str
    startup_order: int = Field(ge=1)


class CanonicalWave(CanonicalModel):
    wave_id: str
    sequence_no: int = Field(ge=1)
    nodes: list[CanonicalScheduledNode]


class CanonicalExecutionSchedule(CanonicalModel):
    mode: Literal["waves"]
    max_parallel_sessions: int = Field(ge=1)
    waves: list[CanonicalWave]


class CanonicalValidationReport(CanonicalModel):
    errors: list[str]
    warnings: list[str]


class CanonicalTrace(CanonicalModel):
    trace_version: str
    run_id: str
    config_hash: str
    tool_catalog_hash: str
    trace_generator_version: str
    llm_metadata: dict[str, Any]
    sessions: list[CanonicalSession] = Field(min_length=1)
    cases: list[CanonicalCase]
    dependency_graph: CanonicalDependencyGraph
    execution_schedule: CanonicalExecutionSchedule
    validation_report: CanonicalValidationReport


def load_canonical_trace(path: str | Path) -> CanonicalTrace:
    trace_path = Path(path)
    try:
        payload = yaml.safe_load(trace_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TraceParseError(f"Invalid YAML in canonical trace '{trace_path}': {exc}") from exc

    if not isinstance(payload, dict):
        raise TraceParseError(f"Invalid canonical trace '{trace_path}': expected a YAML object")

    try:
        trace = CanonicalTrace.model_validate(payload)
    except ValidationError as exc:
        raise TraceParseError(f"Invalid canonical trace '{trace_path}': {exc}") from exc

    _validate_canonical_refs(trace)
    return trace


def build_init_from_sessions(trace: CanonicalTrace, env_values: dict[str, str]) -> SessionInitRecord:
    users: list[SessionInitUser] = []
    for session in trace.sessions:
        username = env_values.get(session.username_env_var)
        if username is None:
            raise TraceParseError(
                f"Session '{session.session_id}' references missing username env var '{session.username_env_var}'"
            )
        password = env_values.get(session.password_env_var)
        login_url = env_values.get(session.login_url_env_var)
        users.append(
            SessionInitUser(
                session_id=session.session_id,
                user_id=session.virtual_actor_id,
                username=username,
                password=password,
                login_url=login_url,
                username_selector=session.username_selector,
                password_selector=session.password_selector,
                submit_selector=session.submit_selector,
                success_selector=session.success_selector,
            )
        )
    return SessionInitRecord(line_number=1, users=users)


def _validate_canonical_refs(trace: CanonicalTrace) -> None:
    node_ids = {node.node_id for node in trace.dependency_graph.nodes}
    case_ids = {case.case_id for case in trace.cases}
    session_ids = {session.session_id for session in trace.sessions}
    scheduled_ids: list[str] = []

    for node in trace.dependency_graph.nodes:
        if node.case_id not in case_ids:
            raise TraceParseError(f"Canonical node '{node.node_id}' references unknown case '{node.case_id}'")
        if node.session_id not in session_ids:
            raise TraceParseError(f"Canonical node '{node.node_id}' references unknown session '{node.session_id}'")

    for edge in trace.dependency_graph.edges:
        if edge.from_ not in node_ids:
            raise TraceParseError(f"Canonical edge references unknown from node '{edge.from_}'")
        if edge.to not in node_ids:
            raise TraceParseError(f"Canonical edge references unknown to node '{edge.to}'")

    for wave in trace.execution_schedule.waves:
        for scheduled_node in wave.nodes:
            if scheduled_node.node_id not in node_ids:
                raise TraceParseError(
                    f"Canonical wave '{wave.wave_id}' references unknown node '{scheduled_node.node_id}'"
                )
            scheduled_ids.append(scheduled_node.node_id)

    duplicates = sorted({node_id for node_id in scheduled_ids if scheduled_ids.count(node_id) > 1})
    if duplicates:
        raise TraceParseError(f"Canonical schedule contains duplicate node ids: {duplicates}")

    unscheduled = sorted(node_ids - set(scheduled_ids))
    if unscheduled:
        raise TraceParseError(f"Canonical schedule omits node ids: {unscheduled}")
