"""Load and validate compiled Pkl generation config."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import (
    Actor,
    ActorCapability,
    BankAccountRules,
    BankAccountDetails,
    BindingSource,
    BindingValueType,
    BusinessDateGate,
    ComputedValue,
    ComputedValueOperator,
    ComputedValueSource,
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
    RealismGuardrails,
    RealismSettings,
    RuntimeDateOverride,
    RoutineScenario,
    RunSettings,
    ScenarioCaseSelection,
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
    "change_vendor_bank_data": {"min": 5, "max": 8},
    "post_outgoing_payment": {"min": 5, "max": 10},
    "revert_vendor_bank_data": {"min": 5, "max": 8},
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
        routine_scenarios=tuple(_routine_scenario(item) for item in payload.get("routineScenarios", [])),
        vendor_bank_accounts=_vendor_bank_accounts(payload.get("vendorBankAccounts", {})),
        computed_values=_computed_values(payload.get("computedValues", {})),
        bank_account_rules=_bank_account_rules(payload.get("bankAccountRules", {})),
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
    if "speedFactor" in item:
        raise TraceGenerationError("Actor field 'speedFactor' is deprecated; use 'delayMultiplier'")
    if "runtimeDelayCapSeconds" in item:
        raise TraceGenerationError(
            "Actor field 'runtimeDelayCapSeconds' has been removed; use marker-local RuntimeDelayBounds"
        )
    guardrails = _realism_guardrails(item.get("realismGuardrails", {}))
    return Actor(
        id=str(item["id"]),
        role=str(item["role"]),
        timezone=str(item["timezone"]),
        persona_description=str(item.get("personaDescription", item.get("realismProfile", {}).get("workerType", item["role"]))),
        delay_multiplier=float(item["delayMultiplier"]),
        realism_guardrails=guardrails,
        expose_as=str(item.get("exposeInFinalDatasetAs", item["id"])),
        capabilities=tuple(_actor_capability(value) for value in item.get("capabilities", [])),
    )


def _realism_guardrails(item: dict[str, Any]) -> RealismGuardrails:
    removed_cap_fields = {"runtimeDelayCapSecondsMin", "runtimeDelayCapSecondsMax"} & item.keys()
    if removed_cap_fields:
        raise TraceGenerationError(
            "Realism guardrail fields runtimeDelayCapSecondsMin/runtimeDelayCapSecondsMax have been removed; "
            "use marker-local RuntimeDelayBounds"
        )
    return RealismGuardrails(
        delay_multiplier_min=float(item.get("delayMultiplierMin", 0.5)),
        delay_multiplier_max=float(item.get("delayMultiplierMax", 3.0)),
        workday_deviation_hours_min=float(item.get("workdayDeviationHoursMin", -1.0)),
        workday_deviation_hours_max=float(item.get("workdayDeviationHoursMax", 1.0)),
        pause_duration_minutes_min=int(item.get("pauseDurationMinutesMin", 30)),
        pause_duration_minutes_max=int(item.get("pauseDurationMinutesMax", 75)),
    )


def _actor_capability(item: dict[str, Any]) -> ActorCapability:
    return ActorCapability(
        process_type=str(item["processType"]),
        step_types=tuple(str(value) for value in item.get("stepTypes", [])),
    )


def _technical_user(item: dict[str, Any]) -> TechnicalUser:
    return TechnicalUser(
        id=str(item["id"]),
        username_env_var=str(item["usernameEnvVar"]),
        password_env_var=str(item["passwordEnvVar"]),
        login_url_env_var=str(item.get("loginUrlEnvVar", "SAP_URL")),
        max_concurrent_actor_sessions=int(item["maxConcurrentActorSessions"]),
    )


def _identity_mapping(item: dict[str, Any]) -> IdentityMapping:
    return IdentityMapping(
        synthetic_actor_id=str(item["syntheticActorId"]),
        technical_sap_user_id=str(item["technicalSapUserId"]),
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
        order_multiple=int(item.get("orderMultiple", 1)),
    )


def _process(item: dict[str, Any], tool_requirements: dict[str, Any]) -> ProcessDefinition:
    steps: list[ProcessStep] = []
    raw_steps = item.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise TraceGenerationError(f"Process '{item.get('processType')}' must declare at least one step")
    for step in raw_steps:
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
                same_actor_as_step_id=(
                    str(step["sameActorAsStepId"]) if step.get("sameActorAsStepId") is not None else None
                ),
                input_bindings=tuple(_input_binding(binding, step_type) for binding in step.get("inputBindings", [])),
                planned_date_input_bindings=tuple(
                    _input_binding(binding, step_type) for binding in step.get("plannedDateInputBindings", [])
                ),
                required_sap_object_keys=tuple(str(value) for value in step.get("requiredSapObjectKeys", [])),
                object_output_required=bool(step.get("objectOutputRequired", True)),
                labels=_string_mapping(step.get("labels", {}), "step.labels"),
                business_date_gate=_business_date_gate(step.get("businessDateGate", "none")),
                material_valuation_lock=bool(step.get("materialValuationLock", False)),
                runtime_date_overrides=tuple(
                    _runtime_date_override(value) for value in step.get("runtimeDateOverrides", [])
                ),
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
        scenario_type=str(item.get("scenarioType", "NORMAL")),
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
    if source not in {
        "literal",
        "master_data",
        "case",
        "planned_date",
        "prior_output",
        "derived",
        "vendor_bank_account",
    }:
        raise TraceGenerationError(f"unsupported binding source '{source}'")
    return source  # type: ignore[return-value]


def _binding_value_type(value: object) -> BindingValueType:
    value_type = str(value)
    if value_type not in {"string", "int", "float", "bool"}:
        raise TraceGenerationError(f"unsupported binding valueType '{value_type}'")
    return value_type  # type: ignore[return-value]


def _business_date_gate(value: object) -> BusinessDateGate:
    gate = str(value)
    if gate not in {"none", "delivery_date", "payment_posting_date"}:
        raise TraceGenerationError(f"unsupported businessDateGate '{gate}'")
    return gate  # type: ignore[return-value]


def _runtime_date_override(item: dict[str, Any]) -> RuntimeDateOverride:
    fields = tuple(str(field) for field in item.get("fields", []))
    if not fields:
        raise TraceGenerationError("runtimeDateOverrides entries must declare at least one field")
    runtime_value_policy = str(item["runtimeValuePolicy"])
    if runtime_value_policy not in {"sap_current_date", "executor_current_date"}:
        raise TraceGenerationError(
            f"RuntimeDateOverride runtimeValuePolicy '{runtime_value_policy}' is unsupported"
        )
    source = str(item.get("source", "planned_date_inputs"))
    if source != "planned_date_inputs":
        raise TraceGenerationError(f"RuntimeDateOverride source '{source}' is unsupported")
    return RuntimeDateOverride(
        object_type=str(item["objectType"]),
        fields=fields,
        runtime_value_policy=runtime_value_policy,
        source=source,
        reason=str(item["reason"]),
    )


def _string_mapping(value: object, path: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TraceGenerationError(f"{path} must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _fraud_scenario(item: dict[str, Any]) -> FraudScenario:
    enabled = bool(item["enabled"])
    target_share = float(item["targetShare"])
    if enabled and not 0 < target_share <= 1.0:
        raise TraceGenerationError("Enabled fraud scenarios must have targetShare in range (0, 1.0]")
    return FraudScenario(
        id=str(item["id"]),
        enabled=enabled,
        target_share=target_share,
        case_outcome=_case_outcome(item.get("caseOutcome", "fraud")),
        labels=_string_mapping(item.get("labels", {}), f"fraudScenarios[{item.get('id')}].labels"),
        case_selection=_scenario_case_selection(item.get("caseSelection", {})),
    )


def _case_outcome(value: object) -> str:
    outcome = str(value)
    if outcome not in {"fraud", "non_fraud"}:
        raise TraceGenerationError("caseOutcome must be 'fraud' or 'non_fraud'")
    return outcome


def _scenario_case_selection(item: dict[str, Any] | None) -> ScenarioCaseSelection:
    if item is None:
        return ScenarioCaseSelection()
    if not isinstance(item, dict):
        raise TraceGenerationError("caseSelection must be a mapping")
    fixed_vendor_id = item.get("fixedVendorId")
    return ScenarioCaseSelection(
        fixed_vendor_id=str(fixed_vendor_id) if fixed_vendor_id is not None else None,
    )


def _vendor_bank_accounts(items: dict[str, Any]) -> dict[str, BankAccountDetails]:
    if not isinstance(items, dict):
        raise TraceGenerationError("Configuration field 'vendorBankAccounts' must be a mapping")
    return {
        str(vendor_id): _bank_account_details(item)
        for vendor_id, item in items.items()
    }


def _routine_scenario(item: dict[str, Any]) -> RoutineScenario:
    enabled = bool(item["enabled"])
    target_share = float(item["targetShare"])
    if enabled and not 0 < target_share <= 1.0:
        raise TraceGenerationError("Enabled routine scenarios must have targetShare in range (0, 1.0]")
    return RoutineScenario(
        id=str(item["id"]),
        enabled=enabled,
        target_share=target_share,
        case_outcome=_case_outcome(item.get("caseOutcome", "non_fraud")),
        labels=_string_mapping(item.get("labels", {}), f"routineScenarios[{item.get('id')}].labels"),
        case_selection=_scenario_case_selection(item.get("caseSelection", {})),
    )


def _computed_values(items: dict[str, Any]) -> dict[str, ComputedValue]:
    if not isinstance(items, dict):
        raise TraceGenerationError("Configuration field 'computedValues' must be a mapping")
    return {
        str(name): _computed_value(item)
        for name, item in items.items()
    }


def _computed_value(item: dict[str, Any]) -> ComputedValue:
    return ComputedValue(
        source=_computed_value_source(item.get("source", "case")),
        field=str(item["field"]),
        operator=_computed_value_operator(item["operator"]),
        factor=float(item["factor"]),
        precision=int(item.get("precision", 3)),
    )


def _computed_value_source(value: object) -> ComputedValueSource:
    source = str(value)
    if source != "case":
        raise TraceGenerationError(f"unsupported computed value source '{source}'")
    return source  # type: ignore[return-value]


def _computed_value_operator(value: object) -> ComputedValueOperator:
    operator = str(value)
    if operator != "multiply":
        raise TraceGenerationError(f"unsupported computed value operator '{operator}'")
    return operator  # type: ignore[return-value]


def _bank_account_rules(item: dict[str, Any]) -> BankAccountRules:
    if not isinstance(item, dict):
        raise TraceGenerationError("Configuration field 'bankAccountRules' must be a mapping")
    return BankAccountRules(
        allowed_bank_keys=tuple(str(value) for value in item.get("allowedBankKeys", [])),
        account_number_min_length=int(item.get("accountNumberMinLength", 0)),
        account_number_max_length=int(item.get("accountNumberMaxLength", 1000)),
        require_numeric_account_number=bool(item.get("requireNumericAccountNumber", False)),
    )


def _bank_account_details(item: dict[str, Any]) -> BankAccountDetails:
    return BankAccountDetails(
        bank_key=str(item["bankKey"]),
        account_number=str(item["accountNumber"]),
        account_owner=str(item["accountOwner"]),
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
        max_parallel_actor_sessions=int(item["maxParallelActorSessions"]),
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
        realism=_realism_settings(item.get("realism", {})),
    )


def _realism_settings(item: dict[str, Any]) -> RealismSettings:
    return RealismSettings(
        enabled=bool(item.get("enabled", False)),
        max_retries=int(item.get("maxRetries", 3)),
        cache_dir=str(item.get("cacheDir", "configuration/build")),
        daily_case_count_min=int(item.get("dailyCaseCountMin", 0)),
        daily_case_count_max=int(item.get("dailyCaseCountMax", 10000)),
        max_price_variation_pct=float(item.get("maxPriceVariationPct", 0.05)),
        max_daily_price_trend_pct=float(item.get("maxDailyPriceTrendPct", 0.01)),
        max_workload_delay_multiplier_boost=float(item.get("maxWorkloadDelayMultiplierBoost", 0.25)),
        max_workload_workday_deviation_hours_boost=float(item.get("maxWorkloadWorkdayDeviationHoursBoost", 0.5)),
        relative_demand_weight_min=int(item.get("relativeDemandWeightMin", 1)),
        relative_demand_weight_max=int(item.get("relativeDemandWeightMax", 100)),
        quantity_variation_pct_min=float(item.get("quantityVariationPctMin", 0.05)),
        quantity_variation_pct_max=float(item.get("quantityVariationPctMax", 0.5)),
        max_bulk_order_share=float(item.get("maxBulkOrderShare", 0.35)),
        allowed_order_multiples=tuple(int(value) for value in item.get("allowedOrderMultiples", [1, 5, 10, 20, 25, 50])),
        max_material_share_per_horizon=(
            None
            if item.get("maxMaterialSharePerHorizon") is None
            else float(item["maxMaterialSharePerHorizon"])
        ),
        require_all_active_materials_in_demand_profile=bool(
            item.get("requireAllActiveMaterialsInDemandProfile", True)
        ),
        material_valuation_lock_enabled=bool(item.get("materialValuationLockEnabled", True)),
        material_valuation_lock_buffer_seconds=int(item.get("materialValuationLockBufferSeconds", 120)),
        blocked_materials=tuple(str(value) for value in item.get("blockedMaterials", [])),
    )


def _validate(config: GenerationConfig) -> None:
    if config.run_settings.queue_policy != "fifo":
        raise TraceGenerationError("Only FIFO queue policy is supported in trace generator v1")
    if len(config.run_settings.active_process_types) != 1:
        raise TraceGenerationError("Trace generator v1 supports exactly one active process type")
    if config.run_settings.realism.max_retries < 1:
        raise TraceGenerationError("runSettings.realism.maxRetries must be >= 1")
    if config.run_settings.realism.daily_case_count_min > config.run_settings.realism.daily_case_count_max:
        raise TraceGenerationError("runSettings.realism daily case count min must be <= max")
    active_process_types = set(config.run_settings.active_process_types)
    process_types = {process.process_type for process in config.processes}
    missing_processes = active_process_types - process_types
    if missing_processes:
        raise TraceGenerationError(f"Active process type not configured: {sorted(missing_processes)}")
    scenario_types_for_run = {"NORMAL"}
    enabled_fraud_scenarios = tuple(scenario for scenario in config.fraud_scenarios if scenario.enabled)
    enabled_routine_scenarios = tuple(scenario for scenario in config.routine_scenarios if scenario.enabled)
    target_share_total = sum(scenario.target_share for scenario in (*enabled_fraud_scenarios, *enabled_routine_scenarios))
    if target_share_total > 1.0:
        raise TraceGenerationError("Enabled scenario targetShare total must be <= 1.0")
    for scenario in enabled_fraud_scenarios:
        if not 0 < scenario.target_share <= 1.0:
            raise TraceGenerationError("Enabled fraud scenarios must have targetShare in range (0, 1.0]")
        scenario_types_for_run.add(scenario.id)
    for scenario in enabled_routine_scenarios:
        if not 0 < scenario.target_share <= 1.0:
            raise TraceGenerationError("Enabled routine scenarios must have targetShare in range (0, 1.0]")
        scenario_types_for_run.add(scenario.id)
    scenario_processes = {
        (process.process_type, process.scenario_type)
        for process in config.processes
    }
    for process_type in active_process_types:
        if (process_type, "NORMAL") not in scenario_processes:
            raise TraceGenerationError(
                f"No NORMAL process variant configured for active process '{process_type}'"
            )
        for scenario_type in sorted(scenario_types_for_run - {"NORMAL"}):
            if (process_type, scenario_type) not in scenario_processes:
                raise TraceGenerationError(
                    f"No process variant configured for active process '{process_type}' and scenario '{scenario_type}'"
                )
    master_material_ids = {item.material_id for item in config.master_data}
    blocked_material_ids = set(config.run_settings.realism.blocked_materials)
    unknown_blocked_materials = sorted(
        blocked_material_ids - master_material_ids
    )
    if unknown_blocked_materials:
        raise TraceGenerationError(
            f"runSettings.realism.blockedMaterials references unknown material(s): {unknown_blocked_materials}"
        )
    if len(blocked_material_ids) >= len(master_material_ids):
        raise TraceGenerationError("runSettings.realism.blockedMaterials cannot block all configured materials")
    allowed_order_multiples = set(config.run_settings.realism.allowed_order_multiples)
    for item in config.master_data:
        if item.order_multiple not in allowed_order_multiples:
            raise TraceGenerationError(
                f"masterData orderMultiple for material '{item.material_id}' must be listed in "
                "runSettings.realism.allowedOrderMultiples"
            )
    _validate_bank_accounts(config)

    actor_ids = {actor.id for actor in config.actors}
    technical_user_ids = {user.id for user in config.technical_users}
    for mapping in config.identity_mappings:
        if mapping.synthetic_actor_id not in actor_ids:
            raise TraceGenerationError(f"Identity mapping references unknown actor '{mapping.synthetic_actor_id}'")
        if mapping.technical_sap_user_id not in technical_user_ids:
            raise TraceGenerationError(f"Identity mapping references unknown technical user '{mapping.technical_sap_user_id}'")

    mapped_actor_ids = {mapping.synthetic_actor_id for mapping in config.identity_mappings}
    _validate_actor_capabilities(config)
    for scenario_type in sorted(scenario_types_for_run):
        active_process = config.process_for_scenario(scenario_type)
        _validate_same_actor_affinity(config, active_process)
        for step in active_process.steps:
            capable_actors = config.actors_capable_of(active_process.process_type, step.step_type)
            if not capable_actors:
                raise TraceGenerationError(f"Step '{step.step_type}' has no capable actor")
            unmapped_actor_ids = sorted(actor.id for actor in capable_actors if actor.id not in mapped_actor_ids)
            if unmapped_actor_ids:
                raise TraceGenerationError(
                    f"Capable actor(s) for step '{step.step_type}' have no technical user mapping: {unmapped_actor_ids}"
                )
            if step.step_type not in config.run_settings.step_duration_minutes:
                raise TraceGenerationError(f"Step '{step.step_type}' has no step duration range")
            if step.object_output_required and not step.required_sap_object_keys:
                raise TraceGenerationError(f"Step '{step.step_type}' has no required SAP object keys")
            required_fields = set(config.tool_requirements[step.tool_name].required_input_fields)
            bound_fields = _bound_root_fields(step.input_bindings)
            missing_bindings = sorted(required_fields - bound_fields)
            if missing_bindings:
                missing = ", ".join(missing_bindings)
                raise TraceGenerationError(f"Step '{step.step_type}' missing bindings for required fields: {missing}")
            all_bindings = step.input_bindings + step.planned_date_input_bindings
            unknown_binding_steps = {binding.step_type for binding in all_bindings if binding.step_type != step.step_type}
            if unknown_binding_steps:
                raise TraceGenerationError(f"Step '{step.step_type}' has binding with mismatched stepType")

        step_types = {step.step_type for step in active_process.steps}
        for dep in active_process.dependencies:
            if dep.from_step_type not in step_types or dep.to_step_type not in step_types:
                raise TraceGenerationError(
                    f"Dependency '{dep.from_step_type}->{dep.to_step_type}' references unknown step"
                )
        _validate_acyclic(active_process)
    ensure_fraud_scenarios_supported(
        config.fraud_scenarios,
        supported_scenario_types={process.scenario_type for process in config.processes},
    )


def _validate_bank_accounts(config: GenerationConfig) -> None:
    for vendor_id, account in config.vendor_bank_accounts.items():
        _validate_bank_account_details(f"vendorBankAccounts[{vendor_id!r}]", account, config.bank_account_rules)
    for scenario in config.fraud_scenarios:
        _validate_scenario_case_selection(config, scenario.id, scenario.case_selection)
    for scenario in config.routine_scenarios:
        _validate_scenario_case_selection(config, scenario.id, scenario.case_selection)
    if _uses_vendor_bank_account_bindings(config):
        configured_vendors = set(config.vendor_bank_accounts)
        required_vendors = {
            vendor_id
            for item in config.master_data
            if item.material_id not in set(config.run_settings.realism.blocked_materials)
            for vendor_id in item.valid_vendors
        }
        missing_vendors = sorted(required_vendors - configured_vendors)
        if missing_vendors:
            raise TraceGenerationError(
                f"vendorBankAccounts missing account details for configured vendor(s): {missing_vendors}"
            )


def _validate_scenario_case_selection(
    config: GenerationConfig,
    scenario_id: str,
    case_selection: ScenarioCaseSelection,
) -> None:
    if case_selection.fixed_vendor_id is None:
        return
    configured_vendors = {
        vendor_id
        for item in config.master_data
        if item.material_id not in set(config.run_settings.realism.blocked_materials)
        for vendor_id in item.valid_vendors
    }
    if case_selection.fixed_vendor_id not in configured_vendors:
        raise TraceGenerationError(
            f"Scenario '{scenario_id}' caseSelection.fixedVendorId references unknown or blocked vendor "
            f"'{case_selection.fixed_vendor_id}'"
        )


def _validate_bank_account_details(path: str, account: BankAccountDetails, rules: BankAccountRules) -> None:
    if rules.allowed_bank_keys and account.bank_key not in rules.allowed_bank_keys:
        raise TraceGenerationError(
            f"{path} bankKey must be one of {sorted(rules.allowed_bank_keys)}"
        )
    if not (rules.account_number_min_length <= len(account.account_number) <= rules.account_number_max_length):
        raise TraceGenerationError(
            f"{path} accountNumber must contain {rules.account_number_min_length} to "
            f"{rules.account_number_max_length} characters"
        )
    if rules.require_numeric_account_number and not account.account_number.isdigit():
        raise TraceGenerationError(f"{path} accountNumber must contain only digits")


def _uses_vendor_bank_account_bindings(config: GenerationConfig) -> bool:
    return any(
        binding.source == "vendor_bank_account"
        for process in config.processes
        for step in process.steps
        for binding in step.input_bindings
    )


def _bound_root_fields(bindings: tuple[InputBinding, ...]) -> set[str]:
    return {binding.field.split(".", maxsplit=1)[0] for binding in bindings}


def _validate_same_actor_affinity(config: GenerationConfig, process: ProcessDefinition) -> None:
    prior_steps_by_id: dict[str, ProcessStep] = {}
    for step in process.steps:
        if step.same_actor_as_step_id is not None:
            prior_step = prior_steps_by_id.get(step.same_actor_as_step_id)
            if prior_step is None:
                raise TraceGenerationError(
                    f"Process '{process.process_type}' scenario '{process.scenario_type}' step "
                    f"'{step.step_id}' sameActorAsStepId must reference an earlier step"
                )
            prior_actor_ids = {
                actor.id
                for actor in config.actors_capable_of(process.process_type, prior_step.step_type)
            }
            current_actor_ids = {
                actor.id
                for actor in config.actors_capable_of(process.process_type, step.step_type)
            }
            missing_actor_ids = sorted(prior_actor_ids - current_actor_ids)
            if missing_actor_ids:
                raise TraceGenerationError(
                    f"Process '{process.process_type}' scenario '{process.scenario_type}' step "
                    f"'{step.step_id}' sameActorAsStepId references step '{prior_step.step_id}', "
                    f"but actor(s) {missing_actor_ids} cannot execute both steps"
                )
        prior_steps_by_id[step.step_id] = step


def _validate_actor_capabilities(config: GenerationConfig) -> None:
    process_step_types: dict[str, set[str]] = {}
    for process in config.processes:
        process_step_types.setdefault(process.process_type, set()).update(
            step.step_type for step in process.steps
        )
    process_types = set(process_step_types)
    for actor in config.actors:
        _validate_actor_realism(actor)
        for capability in actor.capabilities:
            if capability.process_type not in process_types:
                raise TraceGenerationError(
                    f"Actor '{actor.id}' capability references unknown process '{capability.process_type}'"
                )
            unknown_steps = sorted(set(capability.step_types) - process_step_types[capability.process_type])
            if unknown_steps:
                raise TraceGenerationError(
                    f"Actor '{actor.id}' capability references unknown step type(s): {unknown_steps}"
                )


def _validate_actor_realism(actor: Actor) -> None:
    guardrails = actor.realism_guardrails
    if not guardrails.delay_multiplier_min <= actor.delay_multiplier <= guardrails.delay_multiplier_max:
        raise TraceGenerationError(
            f"Actor '{actor.id}' delayMultiplier must be within realism guardrails "
            f"[{guardrails.delay_multiplier_min}, {guardrails.delay_multiplier_max}]"
        )


def _validate_acyclic(process: ProcessDefinition) -> None:
    dependencies: dict[str, list[str]] = {step.step_type: [] for step in process.steps}
    for dep in process.dependencies:
        dependencies[dep.from_step_type].append(dep.to_step_type)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_type: str) -> None:
        if step_type in visiting:
            raise TraceGenerationError("Process dependency graph must be acyclic")
        if step_type in visited:
            return
        visiting.add(step_type)
        for child in dependencies[step_type]:
            visit(child)
        visiting.remove(step_type)
        visited.add(step_type)

    for step in dependencies:
        visit(step)
