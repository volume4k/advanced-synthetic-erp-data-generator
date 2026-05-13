"""Pydantic schemas for trace-generator artifacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceLineItem(ArtifactModel):
    line_id: str
    material_id: str
    vendor_id: str
    plant: str
    purchasing_org: str
    storage_location: str
    quantity: int
    target_price: float


class TraceCase(ArtifactModel):
    case_id: str
    process_type: str
    scenario_id: str
    case_label: str
    line_items: list[TraceLineItem]


class TargetSyntheticTime(ArtifactModel):
    start: str
    end: str


class TraceNode(ArtifactModel):
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
    target_synthetic_time: TargetSyntheticTime
    labels: dict[str, str]


class TraceEdge(ArtifactModel):
    from_: str = Field(alias="from")
    to: str
    type: str
    reason: str


class DependencyGraph(ArtifactModel):
    nodes: list[TraceNode]
    edges: list[TraceEdge]


class ScheduledNode(ArtifactModel):
    node_id: str
    startup_order: int


class ExecutionWave(ArtifactModel):
    wave_id: str
    sequence_no: int
    nodes: list[ScheduledNode]


class ExecutionSchedule(ArtifactModel):
    mode: Literal["waves"]
    max_parallel_sessions: int
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
    cases: list[TraceCase]
    dependency_graph: DependencyGraph
    execution_schedule: ExecutionSchedule
    validation_report: ValidationReport


class TimestampPolicy(ArtifactModel):
    source: str
    preserve_process_order: bool
    generator_real_time_is_not_synthetic_time: bool


class ActorProjection(ArtifactModel):
    virtual_actor_id: str
    technical_user_id: str
    session_id: str
    expose_as: str


class CaseLabel(ArtifactModel):
    case_id: str
    scenario_id: str
    case_label: str


class NodeTimestamp(ArtifactModel):
    node_id: str
    case_id: str
    step_type: str
    target_synthetic_start: str
    target_synthetic_end: str
    business_dates: dict[str, str]


class ExpectedObjectKeys(ArtifactModel):
    node_id: str
    case_id: str
    expected_outputs: list[str]


class ObjectLineage(ArtifactModel):
    case_id: str
    chain: list[str]


class FailedCasePolicy(ArtifactModel):
    exclude_failed_cases: bool
    source_artifacts: list[str]


class PostProcessingExport(ArtifactModel):
    id: str
    description: str


class PostProcessingManifestArtifact(ArtifactModel):
    manifest_version: str
    run_id: str
    config_hash: str
    timestamp_policy: TimestampPolicy
    actor_projection: list[ActorProjection]
    case_labels: list[CaseLabel]
    node_timestamps: list[NodeTimestamp]
    expected_object_keys: list[ExpectedObjectKeys]
    object_lineage: list[ObjectLineage]
    post_processing_exports: list[PostProcessingExport]
    failed_case_policy: FailedCasePolicy
