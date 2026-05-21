"""Pydantic schemas for trace-generator artifacts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceLineItem(ArtifactModel):
    line_id: str
    material_id: str
    vendor_id: str
    plant: str
    purchasing_org: str
    storage_location: str
    quantity: int = Field(ge=1)
    target_price: Decimal = Field(ge=Decimal("0"))

    @field_serializer("target_price", when_used="json")
    def serialize_target_price(self, value: Decimal) -> float:
        return float(value)


class TraceCase(ArtifactModel):
    case_id: str
    process_type: str
    case_scenario_type: str
    requested_delivery_date: str | None = None
    line_items: list[TraceLineItem]


class HumanDelayProfile(ArtifactModel):
    delay_multiplier: float = Field(gt=0)
    runtime_delay_cap_seconds: float = Field(ge=0)


class TraceActorSession(ArtifactModel):
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


class PlannedSyntheticTime(ArtifactModel):
    start: str
    end: str


class TracePlannedStep(ArtifactModel):
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
    planned_synthetic_time: PlannedSyntheticTime
    labels: dict[str, str]


class TraceDependency(ArtifactModel):
    from_planned_step_id: str
    to_planned_step_id: str
    type: str
    reason: str


class DependencyGraph(ArtifactModel):
    planned_steps: list[TracePlannedStep]
    dependencies: list[TraceDependency]


class ScheduledPlannedStep(ArtifactModel):
    planned_step_id: str
    startup_order: int = Field(ge=1)


class ExecutionWave(ArtifactModel):
    wave_id: str
    sequence_no: int = Field(ge=1)
    planned_steps: list[ScheduledPlannedStep]


class ExecutionSchedule(ArtifactModel):
    mode: Literal["waves"]
    max_parallel_actor_sessions: int = Field(ge=1)
    waves: list[ExecutionWave]


class ValidationReport(ArtifactModel):
    errors: list[str]
    warnings: list[str]


class ExecutionTraceArtifact(ArtifactModel):
    trace_version: str
    run_id: str
    config_hash: str
    tool_catalog_hash: str
    trace_generator_version: str
    llm_metadata: dict[str, Any]
    actor_sessions: list[TraceActorSession]
    cases: list[TraceCase]
    dependency_graph: DependencyGraph
    execution_schedule: ExecutionSchedule
    validation_report: ValidationReport


class TimestampPolicy(ArtifactModel):
    source: str
    preserve_process_order: bool
    generator_real_time_is_not_synthetic_time: bool


class ActorProjection(ArtifactModel):
    synthetic_actor_id: str
    technical_sap_user_id: str
    actor_session_id: str
    expose_as: str


class CaseScenarioType(ArtifactModel):
    case_id: str
    case_scenario_type: str


class PlannedStepTimestamp(ArtifactModel):
    planned_step_id: str
    case_id: str
    step_type: str
    planned_synthetic_start: str
    planned_synthetic_end: str
    planned_date_inputs: dict[str, str]


class RequiredSapObjectKeys(ArtifactModel):
    planned_step_id: str
    case_id: str
    required_sap_object_keys: list[str]


class ObjectLineage(ArtifactModel):
    case_id: str
    chain: list[str]


class FailedProcessCasePolicy(ArtifactModel):
    exclude_failed_cases: bool
    source_artifacts: list[str]


class PostProcessingExport(ArtifactModel):
    id: str
    description: str


class PlannedDateInputOverride(ArtifactModel):
    planned_step_id: str
    case_id: str
    step_type: str
    object_type: str
    field: str
    planned_value: str
    runtime_value_policy: Literal["sap_current_date", "executor_current_date"]
    source: Literal["planned_date_inputs"]
    reason: str


class PostProcessingManifestArtifact(ArtifactModel):
    manifest_version: str
    run_id: str
    config_hash: str
    realism_criteria_hash: str | None = None
    timestamp_policy: TimestampPolicy
    actor_projection: list[ActorProjection]
    case_scenario_types: list[CaseScenarioType]
    planned_step_timestamps: list[PlannedStepTimestamp]
    required_sap_object_keys: list[RequiredSapObjectKeys]
    object_lineage: list[ObjectLineage]
    post_processing_exports: list[PostProcessingExport]
    planned_date_input_overrides: list[PlannedDateInputOverride]
    failed_process_case_policy: FailedProcessCasePolicy
