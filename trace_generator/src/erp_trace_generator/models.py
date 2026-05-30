"""Typed trace-generator planning models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ActorCapability:
    process_type: str
    step_types: tuple[str, ...]


@dataclass(frozen=True)
class RealismGuardrails:
    delay_multiplier_min: float
    delay_multiplier_max: float
    workday_deviation_hours_min: float
    workday_deviation_hours_max: float
    pause_duration_minutes_min: int
    pause_duration_minutes_max: int

    def __post_init__(self) -> None:
        if self.delay_multiplier_min > self.delay_multiplier_max:
            raise ValueError("delay_multiplier_min must be <= delay_multiplier_max")
        if self.workday_deviation_hours_min > self.workday_deviation_hours_max:
            raise ValueError("workday_deviation_hours_min must be <= workday_deviation_hours_max")
        if self.pause_duration_minutes_min > self.pause_duration_minutes_max:
            raise ValueError("pause_duration_minutes_min must be <= pause_duration_minutes_max")


@dataclass(frozen=True)
class Actor:
    id: str
    role: str
    timezone: str
    persona_description: str
    delay_multiplier: float
    realism_guardrails: RealismGuardrails
    expose_as: str
    capabilities: tuple[ActorCapability, ...]


@dataclass(frozen=True)
class TechnicalUser:
    id: str
    username_env_var: str
    password_env_var: str
    login_url_env_var: str
    max_concurrent_actor_sessions: int


@dataclass(frozen=True)
class IdentityMapping:
    synthetic_actor_id: str
    technical_sap_user_id: str


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
    order_multiple: int = 1

    def __post_init__(self) -> None:
        if self.quantity_min > self.quantity_max:
            raise ValueError("quantity_min must be <= quantity_max")
        if self.order_multiple < 1:
            raise ValueError("order_multiple must be >= 1")
        if self.price_min > self.price_max:
            raise ValueError("price_min must be <= price_max")
        if self.delivery_lead_time_min_days > self.delivery_lead_time_max_days:
            raise ValueError("delivery_lead_time_min_days must be <= delivery_lead_time_max_days")


@dataclass(frozen=True)
class ToolRequirement:
    tool_name: str
    required_input_fields: tuple[str, ...]


BindingSource = Literal[
    "literal",
    "master_data",
    "case",
    "planned_date",
    "prior_output",
    "derived",
    "vendor_bank_account",
]
BindingValueType = Literal["string", "int", "float", "bool"]
BusinessDateGate = Literal["none", "delivery_date", "payment_posting_date"]
ComputedValueSource = Literal["case"]
ComputedValueOperator = Literal["multiply"]


@dataclass(frozen=True)
class InputBinding:
    step_type: str
    field: str
    source: BindingSource
    value: str
    value_type: BindingValueType = "string"


@dataclass(frozen=True)
class RuntimeDateOverride:
    object_type: str
    fields: tuple[str, ...]
    runtime_value_policy: str
    source: str
    reason: str


@dataclass(frozen=True)
class ProcessStep:
    step_id: str
    step_type: str
    tool_name: str
    input_bindings: tuple[InputBinding, ...] = ()
    planned_date_input_bindings: tuple[InputBinding, ...] = ()
    required_sap_object_keys: tuple[str, ...] = ()
    object_output_required: bool = True
    labels: dict[str, str] = field(default_factory=dict)
    business_date_gate: BusinessDateGate = "none"
    material_valuation_lock: bool = False
    runtime_date_overrides: tuple[RuntimeDateOverride, ...] = ()


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
    scenario_type: str = "NORMAL"


@dataclass(frozen=True)
class MinuteRange:
    min: int
    max: int

    def __post_init__(self) -> None:
        if self.min > self.max:
            raise ValueError("min must be <= max")


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
    max_parallel_actor_sessions: int
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
    realism: "RealismSettings"


@dataclass(frozen=True)
class PostProcessingExportGroup:
    id: str
    description: str


@dataclass(frozen=True)
class RealismSettings:
    enabled: bool = False
    max_retries: int = 3
    cache_dir: str = "configuration/build"
    daily_case_count_min: int = 0
    daily_case_count_max: int = 10000
    max_price_variation_pct: float = 0.05
    max_daily_price_trend_pct: float = 0.01
    max_workload_delay_multiplier_boost: float = 0.25
    max_workload_workday_deviation_hours_boost: float = 0.5
    relative_demand_weight_min: int = 1
    relative_demand_weight_max: int = 100
    quantity_variation_pct_min: float = 0.05
    quantity_variation_pct_max: float = 0.5
    max_bulk_order_share: float = 0.35
    allowed_order_multiples: tuple[int, ...] = (1, 5, 10, 20, 25, 50)
    max_material_share_per_horizon: float | None = None
    require_all_active_materials_in_demand_profile: bool = True
    material_valuation_lock_enabled: bool = True
    material_valuation_lock_buffer_seconds: int = 120
    blocked_materials: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.daily_case_count_min < 0:
            raise ValueError("daily_case_count_min must be >= 0")
        if self.daily_case_count_min > self.daily_case_count_max:
            raise ValueError("daily_case_count_min must be <= daily_case_count_max")
        if self.max_price_variation_pct < 0:
            raise ValueError("max_price_variation_pct must be >= 0")
        if self.max_daily_price_trend_pct < 0:
            raise ValueError("max_daily_price_trend_pct must be >= 0")
        if self.max_workload_delay_multiplier_boost < 0:
            raise ValueError("max_workload_delay_multiplier_boost must be >= 0")
        if self.max_workload_workday_deviation_hours_boost < 0:
            raise ValueError("max_workload_workday_deviation_hours_boost must be >= 0")
        if self.relative_demand_weight_min < 1:
            raise ValueError("relative_demand_weight_min must be >= 1")
        if self.relative_demand_weight_min > self.relative_demand_weight_max:
            raise ValueError("relative_demand_weight_min must be <= relative_demand_weight_max")
        if self.quantity_variation_pct_min < 0:
            raise ValueError("quantity_variation_pct_min must be >= 0")
        if self.quantity_variation_pct_min > self.quantity_variation_pct_max:
            raise ValueError("quantity_variation_pct_min must be <= quantity_variation_pct_max")
        if not 0 <= self.max_bulk_order_share <= 1:
            raise ValueError("max_bulk_order_share must be between 0 and 1")
        if not self.allowed_order_multiples or any(value < 1 for value in self.allowed_order_multiples):
            raise ValueError("allowed_order_multiples must contain positive integers")
        if self.max_material_share_per_horizon is not None and not 0 < self.max_material_share_per_horizon <= 1:
            raise ValueError("max_material_share_per_horizon must be between 0 and 1")
        if self.material_valuation_lock_buffer_seconds < 0:
            raise ValueError("material_valuation_lock_buffer_seconds must be >= 0")
        if any(not material_id for material_id in self.blocked_materials):
            raise ValueError("blocked_materials must not contain empty material ids")


@dataclass(frozen=True)
class BankAccountDetails:
    bank_key: str
    account_number: str
    account_owner: str


@dataclass(frozen=True)
class BankAccountRules:
    allowed_bank_keys: tuple[str, ...] = ()
    account_number_min_length: int = 0
    account_number_max_length: int = 1000
    require_numeric_account_number: bool = False


@dataclass(frozen=True)
class ComputedValue:
    source: ComputedValueSource
    field: str
    operator: ComputedValueOperator
    factor: float
    precision: int = 3


@dataclass(frozen=True)
class ScenarioCaseSelection:
    fixed_vendor_id: str | None = None


@dataclass(frozen=True)
class FraudScenario:
    id: str
    enabled: bool
    target_share: float
    case_outcome: str = "fraud"
    labels: dict[str, str] = field(default_factory=dict)
    case_selection: ScenarioCaseSelection = field(default_factory=ScenarioCaseSelection)

    def __post_init__(self) -> None:
        if not 0.0 <= self.target_share <= 1.0:
            raise ValueError("target_share must be between 0 and 1")


@dataclass(frozen=True)
class RoutineScenario:
    id: str
    enabled: bool
    target_share: float
    case_outcome: str = "non_fraud"
    labels: dict[str, str] = field(default_factory=dict)
    case_selection: ScenarioCaseSelection = field(default_factory=ScenarioCaseSelection)

    def __post_init__(self) -> None:
        if not 0.0 <= self.target_share <= 1.0:
            raise ValueError("target_share must be between 0 and 1")


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
    tool_requirements: dict[str, ToolRequirement]
    run_settings: RunSettings
    raw: dict
    fraud_scenarios: tuple[FraudScenario, ...] = ()
    routine_scenarios: tuple[RoutineScenario, ...] = ()
    vendor_bank_accounts: dict[str, BankAccountDetails] = field(default_factory=dict)
    computed_values: dict[str, ComputedValue] = field(default_factory=dict)
    bank_account_rules: BankAccountRules = field(default_factory=BankAccountRules)

    def active_process(self) -> ProcessDefinition:
        return self.process_for_scenario(self.active_scenario_type())

    def process_for_scenario(self, scenario_type: str) -> ProcessDefinition:
        active = self.run_settings.active_process_types
        if not active:
            raise ValueError("active_process_types cannot be empty")
        for process in self.processes:
            if process.process_type == active[0] and process.scenario_type == scenario_type:
                return process
        raise AssertionError("active process existence is validated by loader")

    def active_scenario_type(self) -> str:
        enabled = tuple(scenario for scenario in self.fraud_scenarios if scenario.enabled)
        if not enabled:
            return "NORMAL"
        return enabled[0].id

    def scenario_config(self, scenario_type: str) -> FraudScenario | RoutineScenario:
        if scenario_type == "NORMAL":
            return RoutineScenario(id="NORMAL", enabled=True, target_share=0.0, case_outcome="non_fraud")
        scenario = next(
            (
                item
                for item in (*self.fraud_scenarios, *self.routine_scenarios)
                if item.id == scenario_type
            ),
            None,
        )
        if scenario is None:
            raise AssertionError("scenario existence is validated by loader")
        return scenario

    def actors_capable_of(self, process_type: str, step_type: str) -> tuple[Actor, ...]:
        return tuple(
            actor
            for actor in self.actors
            for capability in actor.capabilities
            if capability.process_type == process_type and step_type in capability.step_types
        )

    def technical_user_for_actor(self, actor_id: str) -> TechnicalUser:
        mapping = next((item for item in self.identity_mappings if item.synthetic_actor_id == actor_id), None)
        if mapping is None:
            raise ValueError(f"No identity mapping found for actor_id: {actor_id}")
        technical_user = next((item for item in self.technical_users if item.id == mapping.technical_sap_user_id), None)
        if technical_user is None:
            raise ValueError(f"No technical user found for technical_sap_user_id: {mapping.technical_sap_user_id}")
        return technical_user


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
    demand_release_time: datetime | None = None
    requested_delivery_date: date | None = None
    case_scenario_type: str = "NORMAL"


@dataclass
class PlannedStep:
    planned_step_id: str
    case_id: str
    step_id: str
    step_type: str
    tool_name: str
    synthetic_actor_id: str
    technical_sap_user_id: str
    actor_session_id: str
    inputs: dict
    required_sap_object_keys: list[str]
    planned_date_inputs: dict[str, str]
    target_start: datetime
    target_end: datetime
    case_scenario_type: str = "NORMAL"
    labels: dict[str, str] = field(default_factory=lambda: {"step_label": "normal"})
    runtime_date_overrides: tuple[RuntimeDateOverride, ...] = ()


@dataclass(frozen=True)
class GeneratedArtifacts:
    execution_trace_path: Path
    post_processing_manifest_path: Path
