"""Load and validate compiled Pkl generation config."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import (
    Actor,
    BindingSource,
    BindingValueType,
    FraudScenario,
    GenerationConfig,
    IdentityMapping,
    InputBinding,
    MasterDataEntry,
    MinuteRange,
    PostProcessingExportGroup,
    ProcessDefinition,
    ProcessDependency,
    ProcessStep,
    RunSettings,
    TechnicalUser,
    ToolRequirement,
    WorkingHours,
)
from erp_trace_generator.fraud import ensure_fraud_scenarios_supported


DEFAULT_STEP_DURATION_MINUTES = {
    "create_purchase_requisition": {"min": 8, "max": 14},
    "create_purchase_order": {"min": 7, "max": 12},
    "post_goods_receipt": {"min": 5, "max": 10},
    "enter_incoming_invoice": {"min": 8, "max": 15},
    "post_outgoing_payment": {"min": 5, "max": 10},
}


def load_generation_config(path: str | Path) -> GenerationConfig:
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TraceGenerationError("Compiled configuration must be a YAML object")

    config = GenerationConfig(
        source_path=config_path,
        version=str(payload.get("version", "")),
        sap_login_url_env_var=str(payload.get("sap", {}).get("loginUrlEnvVar", "SAP_URL")),
        actors=tuple(_actor(item) for item in _list(payload, "actors")),
        technical_users=tuple(_technical_user(item) for item in _list(payload, "technicalUsers")),
        identity_mappings=tuple(_identity_mapping(item) for item in _list(payload, "identityMappings")),
        master_data=tuple(_master_data(item) for item in _list(payload, "masterData")),
        processes=tuple(_process(item, payload.get("toolRequirements", {})) for item in _list(payload, "processes")),
        fraud_scenarios=tuple(_fraud_scenario(item) for item in payload.get("fraudScenarios", [])),
        tool_requirements=_tool_requirements(payload.get("toolRequirements", {})),
        run_settings=_run_settings(payload.get("runSettings", {})),
        raw=payload,
    )
    _validate(config)
    return config


def _list(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise TraceGenerationError(f"Configuration field '{key}' must be a non-empty list")
    return value


def _actor(item: dict[str, Any]) -> Actor:
    return Actor(
        id=str(item["id"]),
        role=str(item["role"]),
        timezone=str(item["timezone"]),
        speed_factor=float(item.get("speedFactor", 1.0)),
        expose_as=str(item.get("exposeInFinalDatasetAs", item["id"])),
    )


def _technical_user(item: dict[str, Any]) -> TechnicalUser:
    return TechnicalUser(
        id=str(item["id"]),
        username_env_var=str(item["usernameEnvVar"]),
        password_env_var=str(item["passwordEnvVar"]),
        login_url_env_var=str(item.get("loginUrlEnvVar", "SAP_URL")),
        max_concurrent_sessions=int(item.get("maxConcurrentSessions", 1)),
    )


def _identity_mapping(item: dict[str, Any]) -> IdentityMapping:
    return IdentityMapping(
        virtual_actor_id=str(item["virtualActorId"]),
        technical_user_id=str(item["technicalUserId"]),
    )


def _master_data(item: dict[str, Any]) -> MasterDataEntry:
    return MasterDataEntry(
        material_id=str(item["materialId"]),
        valid_vendors=tuple(str(value) for value in item["validVendors"]),
        valid_plants=tuple(str(value) for value in item["validPlants"]),
        valid_purchasing_orgs=tuple(str(value) for value in item["validPurchasingOrgs"]),
        valid_storage_locations=tuple(str(value) for value in item["validStorageLocations"]),
        quantity_min=int(item["quantityMin"]),
        quantity_max=int(item["quantityMax"]),
        price_min=float(item["priceMin"]),
        price_max=float(item["priceMax"]),
        currency=str(item["currency"]),
        delivery_lead_time_min_days=int(item["deliveryLeadTimeMinDays"]),
        delivery_lead_time_max_days=int(item["deliveryLeadTimeMaxDays"]),
    )


def _process(item: dict[str, Any], tool_requirements: dict[str, Any]) -> ProcessDefinition:
    steps: list[ProcessStep] = []
    for step in item.get("steps", []):
        tool = step.get("tool")
        if tool is None:
            raise TraceGenerationError(
                f"Process '{item.get('processType')}' step '{step.get('stepType')}' has no tool"
            )
        tool_name = str(tool.get("toolName", ""))
        if tool_name not in tool_requirements:
            raise TraceGenerationError(
                f"Process '{item.get('processType')}' step '{step.get('stepType')}' references unknown tool '{tool_name}'"
            )
        step_type = str(step["stepType"])
        steps.append(
            ProcessStep(
                step_id=str(step["stepId"]),
                step_type=step_type,
                tool_name=tool_name,
                required_role=str(step["requiredRole"]),
                input_bindings=tuple(_input_binding(binding, step_type) for binding in step.get("inputBindings", [])),
                expected_outputs=tuple(str(value) for value in step.get("expectedOutputs", [])),
            )
        )

    return ProcessDefinition(
        process_type=str(item["processType"]),
        steps=tuple(steps),
        dependencies=tuple(
            ProcessDependency(
                from_step_type=str(dep["fromStepType"]),
                to_step_type=str(dep["toStepType"]),
                description=str(dep["description"]),
            )
            for dep in item.get("dependencies", [])
        ),
    )


def _tool_requirements(items: dict[str, Any]) -> dict[str, ToolRequirement]:
    if not isinstance(items, dict) or not items:
        raise TraceGenerationError("Configuration field 'toolRequirements' must be a non-empty mapping")
    return {
        name: ToolRequirement(
            tool_name=str(value["toolName"]),
            required_input_fields=tuple(str(field) for field in value.get("requiredInputFields", [])),
        )
        for name, value in items.items()
    }


def _input_binding(item: dict[str, Any], step_type: str) -> InputBinding:
    return InputBinding(
        step_type=str(item.get("stepType", step_type)),
        field=str(item["field"]),
        source=_binding_source(item["source"]),
        value=str(item["value"]),
        value_type=_binding_value_type(item.get("valueType", "string")),
    )


def _binding_source(value: object) -> BindingSource:
    source = str(value)
    if source not in {"literal", "master_data", "case", "business_date", "prior_output", "derived"}:
        raise TraceGenerationError(f"unsupported binding source '{source}'")
    return source  # type: ignore[return-value]


def _binding_value_type(value: object) -> BindingValueType:
    value_type = str(value)
    if value_type not in {"string", "int", "float", "bool"}:
        raise TraceGenerationError(f"unsupported binding valueType '{value_type}'")
    return value_type  # type: ignore[return-value]


def _fraud_scenario(item: dict[str, Any]) -> FraudScenario:
    return FraudScenario(
        id=str(item["id"]),
        enabled=bool(item["enabled"]),
        target_share=float(item["targetShare"]),
    )


def _run_settings(item: dict[str, Any]) -> RunSettings:
    working_hours = item.get("workingHours", {})
    step_durations = item.get("stepDurationMinutes") or DEFAULT_STEP_DURATION_MINUTES
    inter_step_delays = {
        (str(value["fromStepType"]), str(value["toStepType"])): MinuteRange(min=int(value["min"]), max=int(value["max"]))
        for value in item.get("interStepDelayMinutes", [])
    }
    return RunSettings(
        case_count=int(item["caseCount"]),
        max_parallel_sessions=int(item["maxParallelSessions"]),
        target_timezone=str(item["targetTimezone"]),
        active_process_types=tuple(str(value) for value in item["activeProcessTypes"]),
        scheduler_seed=int(item.get("schedulerSeed", 1)),
        run_start_date=date.fromisoformat(str(item.get("runStartDate", "2026-05-18"))),
        run_horizon_days=int(item.get("runHorizonDays", 30)),
        queue_policy=str(item.get("queuePolicy", "fifo")),
        working_hours=WorkingHours(
            core_start=str(working_hours.get("coreStart", "08:00")),
            core_end=str(working_hours.get("coreEnd", "17:00")),
            daily_deviation_hours_min=float(working_hours.get("dailyDeviationHoursMin", 0.0)),
            daily_deviation_hours_max=float(working_hours.get("dailyDeviationHoursMax", 0.0)),
            pause_window_start=str(working_hours.get("pauseWindowStart", "12:00")),
            pause_window_end=str(working_hours.get("pauseWindowEnd", "13:00")),
            pause_duration_minutes_min=int(working_hours.get("pauseDurationMinutesMin", 30)),
            pause_duration_minutes_max=int(working_hours.get("pauseDurationMinutesMax", 60)),
        ),
        step_duration_minutes={
            str(step_type): MinuteRange(min=int(value["min"]), max=int(value["max"]))
            for step_type, value in step_durations.items()
        },
        inter_step_delay_minutes=inter_step_delays,
        storage_location_labels={str(key): str(value) for key, value in item.get("storageLocationLabels", {}).items()},
        post_processing_export_groups=tuple(
            PostProcessingExportGroup(id=str(value["id"]), description=str(value["description"]))
            for value in item.get("postProcessingExportGroups", [])
        ),
    )


def _validate(config: GenerationConfig) -> None:
    if config.run_settings.queue_policy != "fifo":
        raise TraceGenerationError("Only FIFO queue policy is supported in trace generator v1")
    if len(config.run_settings.active_process_types) != 1:
        raise TraceGenerationError("Trace generator v1 supports exactly one active process type")
    active_process_types = set(config.run_settings.active_process_types)
    process_types = {process.process_type for process in config.processes}
    missing_processes = active_process_types - process_types
    if missing_processes:
        raise TraceGenerationError(f"Active process type not configured: {sorted(missing_processes)}")

    actor_ids = {actor.id for actor in config.actors}
    technical_user_ids = {user.id for user in config.technical_users}
    for mapping in config.identity_mappings:
        if mapping.virtual_actor_id not in actor_ids:
            raise TraceGenerationError(f"Identity mapping references unknown actor '{mapping.virtual_actor_id}'")
        if mapping.technical_user_id not in technical_user_ids:
            raise TraceGenerationError(f"Identity mapping references unknown technical user '{mapping.technical_user_id}'")

    mapped_actor_ids = {mapping.virtual_actor_id for mapping in config.identity_mappings}
    active_process = config.active_process()
    roles = {actor.role for actor in config.actors}
    for step in active_process.steps:
        if step.required_role not in roles:
            raise TraceGenerationError(f"Step '{step.step_type}' has no actor for role '{step.required_role}'")
        actor = config.actor_for_role(step.required_role)
        if actor.id not in mapped_actor_ids:
            raise TraceGenerationError(f"Actor '{actor.id}' for step '{step.step_type}' has no technical user mapping")
        if step.step_type not in config.run_settings.step_duration_minutes:
            raise TraceGenerationError(f"Step '{step.step_type}' has no step duration range")
        if not step.expected_outputs:
            raise TraceGenerationError(f"Step '{step.step_type}' has no expected outputs")
        required_fields = set(config.tool_requirements[step.tool_name].required_input_fields)
        bound_fields = {binding.field for binding in step.input_bindings}
        missing_bindings = sorted(required_fields - bound_fields)
        if missing_bindings:
            missing = ", ".join(missing_bindings)
            raise TraceGenerationError(f"Step '{step.step_type}' missing bindings for required fields: {missing}")
        unknown_binding_steps = {binding.step_type for binding in step.input_bindings if binding.step_type != step.step_type}
        if unknown_binding_steps:
            raise TraceGenerationError(f"Step '{step.step_type}' has binding with mismatched stepType")

    step_types = {step.step_type for step in active_process.steps}
    for dep in active_process.dependencies:
        if dep.from_step_type not in step_types or dep.to_step_type not in step_types:
            raise TraceGenerationError(
                f"Dependency '{dep.from_step_type}->{dep.to_step_type}' references unknown step"
            )
    _validate_acyclic(active_process)
    ensure_fraud_scenarios_supported(config.fraud_scenarios)


def _validate_acyclic(process: ProcessDefinition) -> None:
    edges: dict[str, list[str]] = {step.step_type: [] for step in process.steps}
    for dep in process.dependencies:
        edges[dep.from_step_type].append(dep.to_step_type)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_type: str) -> None:
        if step_type in visiting:
            raise TraceGenerationError("Process dependency graph must be acyclic")
        if step_type in visited:
            return
        visiting.add(step_type)
        for child in edges[step_type]:
            visit(child)
        visiting.remove(step_type)
        visited.add(step_type)

    for step in edges:
        visit(step)
