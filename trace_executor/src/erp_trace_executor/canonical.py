"""Canonical execution-trace loading for wave-aware execution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.models import HumanDelayProfile, SessionInitRecord, SessionInitUser


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalActorSession(CanonicalModel):
    actor_session_id: str
    synthetic_actor_id: str
    technical_sap_user_id: str
    username_env_var: str
    password_env_var: str
    login_url_env_var: str
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_selector: str | None = None
    human_delay_profile: HumanDelayProfile | None = None


class CanonicalCase(CanonicalModel):
    case_id: str
    process_type: str
    case_scenario_type: str
    requested_delivery_date: str | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalPlannedSyntheticTime(CanonicalModel):
    start: str
    end: str


class CanonicalPlannedStep(CanonicalModel):
    planned_step_id: str
    case_id: str
    step_type: str
    tool_name: str
    synthetic_actor_id: str
    technical_sap_user_id: str
    actor_session_id: str
    inputs: dict[str, Any]
    required_sap_object_keys: list[str]
    planned_date_inputs: dict[str, str]
    planned_synthetic_time: CanonicalPlannedSyntheticTime
    labels: dict[str, str] = Field(default_factory=dict)


class CanonicalEdge(CanonicalModel):
    from_planned_step_id: str
    to_planned_step_id: str
    type: str
    reason: str


class CanonicalDependencyGraph(CanonicalModel):
    planned_steps: list[CanonicalPlannedStep]
    dependencies: list[CanonicalEdge]


class CanonicalScheduledPlannedStep(CanonicalModel):
    planned_step_id: str
    startup_order: int = Field(ge=1)


class CanonicalWave(CanonicalModel):
    wave_id: str
    sequence_no: int = Field(ge=1)
    planned_steps: list[CanonicalScheduledPlannedStep]


class CanonicalExecutionSchedule(CanonicalModel):
    mode: Literal["waves"]
    max_parallel_actor_sessions: int = Field(ge=1)
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
    realism_criteria_hash: str | None = None
    llm_metadata: dict[str, Any]
    actor_sessions: list[CanonicalActorSession] = Field(min_length=1)
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

    if trace.trace_version != "0.3":
        raise TraceParseError(
            f"Unsupported canonical trace version '{trace.trace_version}' in '{trace_path}'; expected '0.3'"
        )
    _validate_canonical_refs(trace)
    return trace


def build_init_from_actor_sessions(trace: CanonicalTrace, env_values: dict[str, str]) -> SessionInitRecord:
    users: list[SessionInitUser] = []
    for session in trace.actor_sessions:
        username = env_values.get(session.username_env_var)
        if username is None:
            raise TraceParseError(
                f"Actor session '{session.actor_session_id}' references missing username env var "
                f"'{session.username_env_var}'"
            )
        password = env_values.get(session.password_env_var)
        login_url = env_values.get(session.login_url_env_var)
        if password is None:
            raise TraceParseError(
                f"Actor session '{session.actor_session_id}' references missing password env var "
                f"'{session.password_env_var}'"
            )
        if login_url is None:
            raise TraceParseError(
                f"Actor session '{session.actor_session_id}' references missing login URL env var "
                f"'{session.login_url_env_var}'"
            )
        users.append(
            SessionInitUser(
                actor_session_id=session.actor_session_id,
                synthetic_actor_id=session.synthetic_actor_id,
                username=username,
                password=password,
                login_url=login_url,
                username_selector=session.username_selector,
                password_selector=session.password_selector,
                submit_selector=session.submit_selector,
                success_selector=session.success_selector,
                human_delay_profile=session.human_delay_profile,
            )
        )
    return SessionInitRecord(line_number=1, users=users)


def _validate_canonical_refs(trace: CanonicalTrace) -> None:
    planned_step_ids = {planned_step.planned_step_id for planned_step in trace.dependency_graph.planned_steps}
    case_ids = {case.case_id for case in trace.cases}
    sessions_by_id = {session.actor_session_id: session for session in trace.actor_sessions}
    scheduled_ids: list[str] = []

    for planned_step in trace.dependency_graph.planned_steps:
        if planned_step.case_id not in case_ids:
            raise TraceParseError(
                f"Canonical planned step '{planned_step.planned_step_id}' references unknown case '{planned_step.case_id}'"
            )
        session = sessions_by_id.get(planned_step.actor_session_id)
        if session is None:
            raise TraceParseError(
                f"Canonical planned step '{planned_step.planned_step_id}' references unknown actor session "
                f"'{planned_step.actor_session_id}'"
            )
        if planned_step.synthetic_actor_id != session.synthetic_actor_id:
            raise TraceParseError(
                f"Canonical planned step '{planned_step.planned_step_id}' has synthetic_actor_id "
                f"'{planned_step.synthetic_actor_id}' that does not match actor session "
                f"'{planned_step.actor_session_id}' ({session.synthetic_actor_id})"
            )
        if planned_step.technical_sap_user_id != session.technical_sap_user_id:
            raise TraceParseError(
                f"Canonical planned step '{planned_step.planned_step_id}' has technical_sap_user_id "
                f"'{planned_step.technical_sap_user_id}' that does not match actor session "
                f"'{planned_step.actor_session_id}' ({session.technical_sap_user_id})"
            )

    for dependency in trace.dependency_graph.dependencies:
        if dependency.from_planned_step_id not in planned_step_ids:
            raise TraceParseError(
                f"Canonical dependency references unknown from planned step '{dependency.from_planned_step_id}'"
            )
        if dependency.to_planned_step_id not in planned_step_ids:
            raise TraceParseError(
                f"Canonical dependency references unknown to planned step '{dependency.to_planned_step_id}'"
            )

    for wave in trace.execution_schedule.waves:
        for scheduled_step in wave.planned_steps:
            if scheduled_step.planned_step_id not in planned_step_ids:
                raise TraceParseError(
                    f"Canonical wave '{wave.wave_id}' references unknown planned step '{scheduled_step.planned_step_id}'"
                )
            scheduled_ids.append(scheduled_step.planned_step_id)

    duplicates = sorted({planned_step_id for planned_step_id in scheduled_ids if scheduled_ids.count(planned_step_id) > 1})
    if duplicates:
        raise TraceParseError(f"Canonical schedule contains duplicate planned step ids: {duplicates}")

    unscheduled = sorted(planned_step_ids - set(scheduled_ids))
    if unscheduled:
        raise TraceParseError(f"Canonical schedule omits planned step ids: {unscheduled}")
