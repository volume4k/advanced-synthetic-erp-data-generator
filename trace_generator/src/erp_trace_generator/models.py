"""Typed trace-generator planning models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    timezone: str
    speed_factor: float
    expose_as: str


@dataclass(frozen=True)
class TechnicalUser:
    id: str
    username_env_var: str
    password_env_var: str
    login_url_env_var: str
    max_concurrent_sessions: int


@dataclass(frozen=True)
class IdentityMapping:
    virtual_actor_id: str
    technical_user_id: str


@dataclass(frozen=True)
class MasterDataEntry:
    material_id: str
    valid_vendors: tuple[str, ...]
    valid_plants: tuple[str, ...]
    valid_purchasing_orgs: tuple[str, ...]
    valid_storage_locations: tuple[str, ...]
    quantity_min: int
    quantity_max: int
    price_min: float
    price_max: float
    currency: str
    delivery_lead_time_min_days: int
    delivery_lead_time_max_days: int


@dataclass(frozen=True)
class ToolRequirement:
    tool_name: str
    required_input_fields: tuple[str, ...]


BindingSource = Literal["literal", "master_data", "case", "business_date", "prior_output", "derived"]
BindingValueType = Literal["string", "int", "float", "bool"]


@dataclass(frozen=True)
class InputBinding:
    step_type: str
    field: str
    source: BindingSource
    value: str
    value_type: BindingValueType = "string"


@dataclass(frozen=True)
class ProcessStep:
    step_id: str
    step_type: str
    tool_name: str
    required_role: str
    input_bindings: tuple[InputBinding, ...]
    expected_outputs: tuple[str, ...]


@dataclass(frozen=True)
class ProcessDependency:
    from_step_type: str
    to_step_type: str
    description: str


@dataclass(frozen=True)
class ProcessDefinition:
    process_type: str
    steps: tuple[ProcessStep, ...]
    dependencies: tuple[ProcessDependency, ...]


@dataclass(frozen=True)
class MinuteRange:
    min: int
    max: int


@dataclass(frozen=True)
class WorkingHours:
    core_start: str
    core_end: str
    daily_deviation_hours_min: float
    daily_deviation_hours_max: float
    pause_window_start: str
    pause_window_end: str
    pause_duration_minutes_min: int
    pause_duration_minutes_max: int


@dataclass(frozen=True)
class InterStepDelay:
    from_step_type: str
    to_step_type: str
    minutes: MinuteRange


@dataclass(frozen=True)
class RunSettings:
    case_count: int
    max_parallel_sessions: int
    target_timezone: str
    active_process_types: tuple[str, ...]
    scheduler_seed: int
    run_start_date: date
    run_horizon_days: int
    queue_policy: str
    working_hours: WorkingHours
    step_duration_minutes: dict[str, MinuteRange]
    inter_step_delay_minutes: dict[tuple[str, str], MinuteRange]
    storage_location_labels: dict[str, str]
    post_processing_export_groups: tuple["PostProcessingExportGroup", ...]


@dataclass(frozen=True)
class PostProcessingExportGroup:
    id: str
    description: str


@dataclass(frozen=True)
class FraudScenario:
    id: str
    enabled: bool
    target_share: float


@dataclass(frozen=True)
class GenerationConfig:
    source_path: Path
    version: str
    sap_login_url_env_var: str
    actors: tuple[Actor, ...]
    technical_users: tuple[TechnicalUser, ...]
    identity_mappings: tuple[IdentityMapping, ...]
    master_data: tuple[MasterDataEntry, ...]
    processes: tuple[ProcessDefinition, ...]
    fraud_scenarios: tuple[FraudScenario, ...]
    tool_requirements: dict[str, ToolRequirement]
    run_settings: RunSettings
    raw: dict

    def active_process(self) -> ProcessDefinition:
        active = self.run_settings.active_process_types
        for process in self.processes:
            if process.process_type == active[0]:
                return process
        raise AssertionError("active process existence is validated by loader")

    def actor_for_role(self, role: str) -> Actor:
        for actor in self.actors:
            if actor.role == role:
                return actor
        raise AssertionError("role existence is validated by loader")

    def technical_user_for_actor(self, actor_id: str) -> TechnicalUser:
        mapping = next(item for item in self.identity_mappings if item.virtual_actor_id == actor_id)
        return next(item for item in self.technical_users if item.id == mapping.technical_user_id)


@dataclass(frozen=True)
class CasePlan:
    case_id: str
    process_type: str
    material_id: str
    vendor_id: str
    plant: str
    purchasing_org: str
    storage_location: str
    storage_location_label: str
    quantity: int
    target_price: float
    currency: str
    delivery_date: date
    gross_amount: float
    scenario_id: str = "NORMAL"
    case_label: str = "normal"


@dataclass
class PlannedNode:
    node_id: str
    case_id: str
    step_id: str
    step_type: str
    tool_name: str
    virtual_actor_id: str
    technical_user_id: str
    session_id: str
    inputs: dict
    expected_outputs: list[str]
    business_dates: dict[str, str]
    target_start: datetime
    target_end: datetime
    labels: dict[str, str] = field(default_factory=lambda: {"step_label": "normal"})


@dataclass(frozen=True)
class GeneratedArtifacts:
    execution_trace_path: Path
    executor_trace_path: Path
    post_processing_manifest_path: Path
