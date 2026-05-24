from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

import pytest
import yaml

from erp_trace_generator.artifact_models import ExecutionTraceArtifact, PostProcessingManifestArtifact
from erp_trace_generator.artifacts import _human_delay_profile, _post_processing_manifest, _session_records
from erp_trace_generator.bindings import planned_date_inputs_for_step, resolve_step_inputs
from erp_trace_generator.cli import main
from erp_trace_generator.config import load_generation_config
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.fraud import FRAUD_TRANSFORMERS, register_fraud_transformer
from erp_trace_generator.generator import generate_trace_artifacts
from erp_trace_generator.models import CasePlan, FraudScenario, InputBinding, MasterDataEntry, MinuteRange, PlannedStep, ProcessStep
from erp_trace_generator.planning import plan_cases, plan_steps, plan_waves
from erp_trace_generator.realism import ActorRealismCriteria, CompiledRealismCriteria, DemandRelease, default_demand_releases
from erp_trace_generator.schema_export import schema_output_paths
from erp_trace_generator.timeline import TimelinePlanner


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _base_config() -> dict:
    return {
        "version": "0.1.0",
        "sap": {"loginUrlEnvVar": "SAP_URL"},
        "actors": [
            _actor("procurement_01", "procurement", "SAP_USER_1"),
            _actor("warehouse_01", "warehouse", "SAP_USER_2"),
            _actor("accounts_payable_01", "accounts_payable", "SAP_USER_3"),
        ],
        "technicalUsers": [
            _technical_user("GBGEN_P01", "SAP_USER_1"),
            _technical_user("GBGEN_P02", "SAP_USER_2"),
            _technical_user("GBGEN_P03", "SAP_USER_3"),
        ],
        "identityMappings": [
            {"syntheticActorId": "procurement_01", "technicalSapUserId": "GBGEN_P01"},
            {"syntheticActorId": "warehouse_01", "technicalSapUserId": "GBGEN_P02"},
            {"syntheticActorId": "accounts_payable_01", "technicalSapUserId": "GBGEN_P03"},
        ],
        "masterData": [
            {
                "materialId": "MA025",
                "validVendors": ["V17121"],
                "validPlants": ["MI00"],
                "validPurchasingOrgs": ["US00"],
                "validStorageLocations": ["0002"],
                "quantityMin": 10,
                "quantityMax": 10,
                "priceMin": 20.0,
                "priceMax": 20.0,
                "currency": "USD",
                "deliveryLeadTimeMinDays": 5,
                "deliveryLeadTimeMaxDays": 5,
            }
        ],
        "processes": [
            {
                "processType": "procure_to_pay",
                "steps": [
                    _step("A1", "create_purchase_requisition", "fiori.create_purchase_requisition"),
                    _step("A2", "create_purchase_order", "fiori.create_purchase_order"),
                    _step("A3", "post_goods_receipt", "fiori.create_goods_receipt"),
                    _step("A4", "enter_incoming_invoice", "fiori.create_supplier_invoice"),
                    _step("A5", "post_outgoing_payment", "fiori.send_payment"),
                ],
                "dependencies": [
                    _dependency("create_purchase_requisition", "create_purchase_order"),
                    _dependency("create_purchase_order", "post_goods_receipt"),
                    _dependency("post_goods_receipt", "enter_incoming_invoice"),
                    _dependency("enter_incoming_invoice", "post_outgoing_payment"),
                ],
            }
        ],
        "fraudScenarios": [
            {"id": "VENDOR_FLIPFLOP", "enabled": False, "targetShare": 0.0},
            {"id": "LARCENY", "enabled": False, "targetShare": 0.0},
        ],
        "runSettings": {
            "caseCount": 2,
            "maxParallelActorSessions": 2,
            "targetTimezone": "Europe/Berlin",
            "activeProcessTypes": ["procure_to_pay"],
            "schedulerSeed": 17,
            "runStartDate": "2026-05-18",
            "runHorizonDays": 3,
            "queuePolicy": "fifo",
            "workingHours": {
                "coreStart": "08:00",
                "coreEnd": "17:00",
                "dailyDeviationHoursMin": 0.0,
                "dailyDeviationHoursMax": 0.0,
                "pauseWindowStart": "12:00",
                "pauseWindowEnd": "13:00",
                "pauseDurationMinutesMin": 30,
                "pauseDurationMinutesMax": 30,
            },
            "stepDurationMinutes": {
                "create_purchase_requisition": {"min": 8, "max": 8},
                "create_purchase_order": {"min": 7, "max": 7},
                "post_goods_receipt": {"min": 6, "max": 6},
                "enter_incoming_invoice": {"min": 9, "max": 9},
                "post_outgoing_payment": {"min": 5, "max": 5},
            },
            "interStepDelayMinutes": [
                {"fromStepType": "create_purchase_requisition", "toStepType": "create_purchase_order", "min": 30, "max": 30},
                {"fromStepType": "create_purchase_order", "toStepType": "post_goods_receipt", "min": 60, "max": 60},
                {"fromStepType": "post_goods_receipt", "toStepType": "enter_incoming_invoice", "min": 45, "max": 45},
                {"fromStepType": "enter_incoming_invoice", "toStepType": "post_outgoing_payment", "min": 120, "max": 120},
            ],
            "storageLocationLabels": {"0002": "Trading Goods"},
            "postProcessingExportGroups": [
                {"id": "change_documents", "description": "SAP change document exports"},
                {"id": "purchase_orders", "description": "Purchase order header and item exports"},
                {"id": "material_documents", "description": "Goods receipt material document exports"},
                {"id": "supplier_invoices", "description": "Supplier invoice exports"},
                {"id": "accounting_documents", "description": "Payment accounting document exports"},
            ],
        },
        "toolRequirements": {
            "fiori.create_purchase_requisition": _tool(
                "fiori.create_purchase_requisition",
                [
                    "material",
                    "quantity",
                    "valuation_price",
                    "currency",
                    "price_unit",
                    "delivery_date",
                    "plant",
                    "purchasing_group",
                    "purchasing_organization",
                    "company_code",
                ],
            ),
            "fiori.create_purchase_order": _tool(
                "fiori.create_purchase_order",
                ["purchase_requisition", "storage_location", "supplier", "quantity", "net_price"],
            ),
            "fiori.create_goods_receipt": _tool(
                "fiori.create_goods_receipt",
                ["purchase_order", "storage_location"],
            ),
            "fiori.create_supplier_invoice": _tool(
                "fiori.create_supplier_invoice",
                ["gross_amount", "purchase_order", "tax_code"],
            ),
            "fiori.send_payment": _tool(
                "fiori.send_payment",
                ["company_code", "posting_document_date", "supplier", "accounting_document", "general_ledger_account", "amount"],
            ),
        },
    }


def _actor(actor_id: str, role: str, user_prefix: str) -> dict:
    capabilities = {
        "procurement": ["create_purchase_requisition", "create_purchase_order"],
        "warehouse": ["post_goods_receipt"],
        "accounts_payable": ["enter_incoming_invoice", "post_outgoing_payment"],
    }.get(role, [])
    return {
        "id": actor_id,
        "displayName": actor_id,
        "role": role,
        "timezone": "Europe/Berlin",
        "workLocation": "HD00",
        "delayMultiplier": 1.0,
        "personaDescription": f"{role} clerk with normal ERP habits",
        "realismProfile": {
            "workerType": role,
            "workingHoursDeviation": 0.0,
            "pauseCharacteristicsIndex": 10,
        },
        "realismGuardrails": {
            "delayMultiplierMin": 0.8,
            "delayMultiplierMax": 1.4,
            "workdayDeviationHoursMin": -1.0,
            "workdayDeviationHoursMax": 1.0,
            "pauseDurationMinutesMin": 30,
            "pauseDurationMinutesMax": 75,
        },
        "exposeInFinalDatasetAs": actor_id,
        "capabilities": [{"processType": "procure_to_pay", "stepTypes": capabilities}],
    }


def _technical_user(technical_user_id: str, env_prefix: str) -> dict:
    return {
        "id": technical_user_id,
        "usernameEnvVar": f"{env_prefix}_UN",
        "passwordEnvVar": f"{env_prefix}_PW",
        "loginUrlEnvVar": "SAP_URL",
        "maxConcurrentActorSessions": 1,
    }


def _tool(name: str, required_fields: list[str]) -> dict:
    return {
        "toolName": name,
        "title": name.rsplit(".", 1)[-1].replace("_", " ").title(),
        "inputModel": name.rsplit(".", 1)[-1].title().replace("_", "") + "Input",
        "requiredInputFields": required_fields,
        "inputProperties": [{"name": field, "schemaType": "string", "required": True} for field in required_fields],
    }


def _step(step_id: str, step_type: str, tool_name: str) -> dict:
    return {
        "stepId": step_id,
        "stepType": step_type,
        "tool": {"toolName": tool_name, "title": tool_name, "inputModel": "Input", "requiredInputFields": [], "inputProperties": []},
        "inputBindings": _input_bindings(step_type),
        "plannedDateInputBindings": _planned_date_input_bindings(step_type),
        "requiredSapObjectKeys": _required_sap_object_keys(step_type),
    }


def _dependency(from_step_type: str, to_step_type: str) -> dict:
    return {
        "fromStepType": from_step_type,
        "toStepType": to_step_type,
        "description": f"{from_step_type} before {to_step_type}",
    }


def _input_bindings(step_type: str) -> list[dict]:
    return {
        "create_purchase_requisition": [
            _binding("material", "master_data", "materialId"),
            _binding("quantity", "case", "quantity"),
            _binding("valuation_price", "case", "target_price"),
            _binding("currency", "master_data", "currency"),
            _binding("price_unit", "literal", "1", "int"),
            _binding("delivery_date", "derived", "fiori_delivery_date"),
            _binding("plant", "master_data", "plant"),
            _binding("purchasing_group", "literal", "N00"),
            _binding("purchasing_organization", "master_data", "purchasing_org"),
            _binding("company_code", "master_data", "purchasing_org"),
        ],
        "create_purchase_order": [
            _binding("purchase_requisition", "prior_output", "purchase_requisition.pr_number"),
            _binding("storage_location", "case", "storage_location"),
            _binding("supplier", "master_data", "vendor_id"),
            _binding("quantity", "case", "quantity"),
            _binding("net_price", "case", "target_price"),
        ],
        "post_goods_receipt": [
            _binding("purchase_order", "prior_output", "purchase_order.po_number"),
            _binding("storage_location", "derived", "storage_location_label"),
        ],
        "enter_incoming_invoice": [
            _binding("gross_amount", "derived", "gross_amount"),
            _binding("purchase_order", "prior_output", "purchase_order.po_number"),
            _binding("tax_code", "literal", "XI"),
        ],
        "post_outgoing_payment": [
            _binding("company_code", "master_data", "purchasing_org"),
            _binding("posting_document_date", "derived", "fiori_delivery_date"),
            _binding("posting_date", "derived", "fiori_payment_posting_date"),
            _binding("supplier", "master_data", "vendor_id"),
            _binding("accounting_document", "prior_output", "supplier_invoice.invoice_number"),
            _binding("general_ledger_account", "literal", "1800000"),
            _binding("amount", "derived", "gross_amount"),
            _binding("currency", "master_data", "currency"),
        ],
    }[step_type]


def _planned_date_input_bindings(step_type: str) -> list[dict]:
    return {
        "create_purchase_requisition": [_binding("delivery_date", "derived", "fiori_delivery_date")],
        "create_purchase_order": [],
        "post_goods_receipt": [
            _binding("document_date", "derived", "fiori_delivery_date"),
            _binding("posting_date", "derived", "fiori_delivery_date"),
        ],
        "enter_incoming_invoice": [_binding("invoice_date", "derived", "fiori_delivery_date")],
        "post_outgoing_payment": [
            _binding("posting_document_date", "derived", "fiori_delivery_date"),
            _binding("posting_date", "derived", "fiori_payment_posting_date"),
        ],
    }[step_type]


def _binding(field: str, source: str, value: str, value_type: str = "string") -> dict:
    return {"field": field, "source": source, "value": value, "valueType": value_type}


def _required_sap_object_keys(step_type: str) -> list[str]:
    return {
        "create_purchase_requisition": ["purchase_requisition.pr_number"],
        "create_purchase_order": ["purchase_order.po_number"],
        "post_goods_receipt": ["material_document.material_document_number"],
        "enter_incoming_invoice": ["supplier_invoice.invoice_number", "supplier_invoice.fiscal_year"],
        "post_outgoing_payment": ["payment_document.payment_document_number"],
    }[step_type]


def test_config_loader_rejects_active_null_tool(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["steps"][2]["tool"] = None
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="has no tool"):
        load_generation_config(config_path)


def test_config_loader_rejects_deprecated_speed_factor(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["speedFactor"] = 1.0
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="speedFactor.*deprecated.*delayMultiplier"):
        load_generation_config(config_path)


def test_config_loader_loads_delay_multiplier(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["delayMultiplier"] = 1.25
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    config = load_generation_config(config_path)

    assert config.actors[0].delay_multiplier == 1.25


def test_config_loader_rejects_removed_runtime_delay_cap(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["runtimeDelayCapSeconds"] = 4.0
    payload["actors"][0]["realismGuardrails"]["runtimeDelayCapSecondsMin"] = 1.0
    payload["actors"][0]["realismGuardrails"]["runtimeDelayCapSecondsMax"] = 5.0
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="runtimeDelayCapSeconds.*removed"):
        load_generation_config(config_path)


def test_config_loader_validates_realism_guardrails(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["realismGuardrails"]["delayMultiplierMin"] = 2.0
    payload["actors"][0]["realismGuardrails"]["delayMultiplierMax"] = 1.0
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(ValueError, match="delay_multiplier_min"):
        load_generation_config(config_path)


def test_config_loader_validates_realism_settings_guardrails(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {
        "enabled": True,
        "dailyCaseCountMin": 5,
        "dailyCaseCountMax": 4,
    }
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(ValueError, match="daily_case_count_min"):
        load_generation_config(config_path)


def test_config_loader_validates_material_demand_profile_guardrails(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {
        "enabled": True,
        "relativeDemandWeightMin": 10,
        "relativeDemandWeightMax": 5,
    }
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(ValueError, match="relative_demand_weight_min"):
        load_generation_config(config_path)


def test_config_loader_rejects_invalid_material_order_multiple(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {
        "enabled": True,
        "allowedOrderMultiples": [0, 5],
    }
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(ValueError, match="allowed_order_multiples"):
        load_generation_config(config_path)


def test_config_loader_loads_material_valuation_lock_guardrails(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {
        "materialValuationLockEnabled": True,
        "materialValuationLockBufferSeconds": 180,
        "blockedMaterials": ["MA025"],
    }
    payload["masterData"].append(
        {
            **payload["masterData"][0],
            "materialId": "MB026",
        }
    )
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    config = load_generation_config(config_path)

    assert config.run_settings.realism.material_valuation_lock_enabled is True
    assert config.run_settings.realism.material_valuation_lock_buffer_seconds == 180
    assert config.run_settings.realism.blocked_materials == ("MA025",)


def test_config_loader_rejects_unknown_blocked_material(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {"blockedMaterials": ["MISSING"]}
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="blockedMaterials"):
        load_generation_config(config_path)


def test_config_loader_rejects_process_without_steps(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["steps"] = []
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="must declare at least one step"):
        load_generation_config(config_path)


def test_config_loader_rejects_missing_required_input_binding(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["steps"][0]["inputBindings"] = [
        binding for binding in payload["processes"][0]["steps"][0]["inputBindings"] if binding["field"] != "material"
    ]
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="missing bindings.*material"):
        load_generation_config(config_path)


def test_config_loader_rejects_unknown_binding_source(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["steps"][0]["inputBindings"][0]["source"] = "magic"
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="unsupported binding source 'magic'"):
        load_generation_config(config_path)


def test_config_loader_rejects_missing_actor_capability_for_active_step(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][1]["capabilities"][0]["stepTypes"] = []
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="Step 'post_goods_receipt' has no capable actor"):
        load_generation_config(config_path)


def test_config_loader_rejects_actor_capability_for_unknown_process(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["capabilities"][0]["processType"] = "missing_process"
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="unknown process 'missing_process'"):
        load_generation_config(config_path)


def test_config_loader_rejects_actor_capability_for_unknown_step(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"][0]["capabilities"][0]["stepTypes"].append("missing_step")
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="unknown step type"):
        load_generation_config(config_path)


def test_config_loader_rejects_capable_actor_without_identity_mapping(tmp_path: Path) -> None:
    payload = _base_config()
    payload["identityMappings"] = [
        mapping for mapping in payload["identityMappings"] if mapping["syntheticActorId"] != "warehouse_01"
    ]
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="warehouse_01"):
        load_generation_config(config_path)


def test_enabled_unimplemented_fraud_scenario_fails(tmp_path: Path) -> None:
    payload = _base_config()
    payload["fraudScenarios"][0]["enabled"] = True
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="No graph transformer registered"):
        load_generation_config(config_path)


def test_fraud_transformer_registration_rejects_duplicates() -> None:
    def transformer(graph: object) -> object:
        return graph

    try:
        decorated = register_fraud_transformer("TEST_SCENARIO")(transformer)

        assert decorated is transformer
        assert FRAUD_TRANSFORMERS["TEST_SCENARIO"] is transformer
        with pytest.raises(TraceGenerationError, match="already registered"):
            register_fraud_transformer("TEST_SCENARIO")(transformer)
    finally:
        FRAUD_TRANSFORMERS.pop("TEST_SCENARIO", None)


def test_core_dataclass_invariants_fail_fast() -> None:
    with pytest.raises(ValueError, match="quantity_min"):
        MasterDataEntry(
            material_id="M1",
            valid_vendors=("V1",),
            valid_plants=("P1",),
            valid_purchasing_orgs=("O1",),
            valid_storage_locations=("S1",),
            quantity_min=2,
            quantity_max=1,
            price_min=1.0,
            price_max=2.0,
            currency="USD",
            delivery_lead_time_min_days=1,
            delivery_lead_time_max_days=2,
        )
    with pytest.raises(ValueError, match="min must be <= max"):
        MinuteRange(min=2, max=1)
    with pytest.raises(ValueError, match="target_share"):
        FraudScenario(id="BAD", enabled=True, target_share=1.1)


def test_binding_resolver_handles_supported_sources_and_named_derived_values() -> None:
    case = CasePlan(
        case_id="C001",
        process_type="procure_to_pay",
        material_id="MA025",
        vendor_id="V17121",
        plant="MI00",
        purchasing_org="US00",
        storage_location="0002",
        storage_location_label="Trading Goods",
        quantity=10,
        target_price=20.0,
        currency="USD",
        delivery_date=date(2026, 5, 18),
        gross_amount=200.0,
    )
    step = ProcessStep(
        step_id="A1",
        step_type="sample_step",
        tool_name="fiori.sample",
        input_bindings=(
            InputBinding("sample_step", "material", "master_data", "materialId"),
            InputBinding("sample_step", "quantity", "case", "quantity"),
            InputBinding("sample_step", "posting_date", "planned_date", "delivery_date"),
            InputBinding("sample_step", "purchase_order", "prior_output", "purchase_order.po_number"),
            InputBinding("sample_step", "price_unit", "literal", "1", "int"),
            InputBinding("sample_step", "amount", "derived", "gross_amount"),
            InputBinding("sample_step", "document_date", "derived", "fiori_delivery_date"),
            InputBinding("sample_step", "storage_location", "derived", "storage_location_label"),
        ),
        planned_date_input_bindings=(
            InputBinding("sample_step", "document_date", "derived", "fiori_delivery_date"),
            InputBinding("sample_step", "posting_date", "derived", "fiori_payment_posting_date"),
        ),
        required_sap_object_keys=("sample.output",),
    )

    assert resolve_step_inputs(step, case) == {
        "material": "MA025",
        "quantity": 10,
        "posting_date": "2026-05-18",
        "purchase_order": "$purchase_order.po_number",
        "price_unit": 1,
        "amount": 200.0,
        "document_date": "05/18/2026",
        "storage_location": "Trading Goods",
    }
    assert planned_date_inputs_for_step(step, case) == {
        "document_date": "2026-05-18",
        "posting_date": "2026-05-19",
    }


def test_plan_cases_sets_gross_amount_from_quantity_and_target_price(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    tz = ZoneInfo(config.run_settings.target_timezone)

    cases = plan_cases(
        config,
        Random(17),
        demand_releases=[
            DemandRelease(
                "C001",
                datetime(2026, 5, 18, 8, 0, tzinfo=tz),
                "MA025",
                target_quantity=7,
                target_price=12.345,
            ),
            DemandRelease(
                "C002",
                datetime(2026, 5, 18, 8, 30, tzinfo=tz),
                "MA025",
                target_quantity=4,
                target_price=20.0,
            ),
        ],
    )

    assert cases[0].gross_amount == 86.42
    assert cases[1].gross_amount == 80.0


def test_plan_cases_preserves_explicit_empty_demand_releases(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)

    with pytest.raises(ValueError, match="demand_releases must match configured case_count"):
        plan_cases(config, Random(17), demand_releases=[])


def test_default_demand_releases_roll_across_working_hours(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 4
    payload["runSettings"]["runHorizonDays"] = 2
    payload["runSettings"]["workingHours"]["coreEnd"] = "09:00"
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)

    releases = default_demand_releases(config)

    assert [release.release_time.isoformat() for release in releases] == [
        "2026-05-18T08:00:00+02:00",
        "2026-05-18T08:30:00+02:00",
        "2026-05-19T08:00:00+02:00",
        "2026-05-19T08:30:00+02:00",
    ]


def test_default_demand_releases_fail_when_horizon_has_too_few_slots(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 3
    payload["runSettings"]["runHorizonDays"] = 1
    payload["runSettings"]["workingHours"]["coreEnd"] = "09:00"
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)

    with pytest.raises(TraceGenerationError, match="default demand releases cannot fit caseCount"):
        default_demand_releases(config)


def test_binding_resolver_reports_invalid_literal_casts() -> None:
    case = CasePlan(
        case_id="C001",
        process_type="procure_to_pay",
        material_id="MA025",
        vendor_id="V17121",
        plant="MI00",
        purchasing_org="US00",
        storage_location="0002",
        storage_location_label="Trading Goods",
        quantity=10,
        target_price=20.0,
        currency="USD",
        delivery_date=date(2026, 5, 18),
        gross_amount=200.0,
    )
    step = ProcessStep(
        step_id="A1",
        step_type="sample_step",
        tool_name="fiori.sample",
        input_bindings=(InputBinding("sample_step", "enabled", "literal", "maybe", "bool"),),
    )

    with pytest.raises(TraceGenerationError, match="Cannot cast literal 'maybe' to bool"):
        resolve_step_inputs(step, case)


def test_session_records_reject_same_session_for_multiple_actors(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    planned_step_kwargs = {
        "case_id": "C001",
        "step_id": "A1",
        "step_type": "create_purchase_requisition",
        "tool_name": "fiori.create_purchase_requisition",
        "technical_sap_user_id": "GBGEN_P01",
        "actor_session_id": "shared-session",
        "inputs": {},
        "required_sap_object_keys": [],
        "planned_date_inputs": {},
        "target_start": datetime(2026, 5, 18, 8, 0),
        "target_end": datetime(2026, 5, 18, 8, 1),
    }

    with pytest.raises(TraceGenerationError, match="shared-session"):
        _session_records(
            config,
            [
                PlannedStep(planned_step_id="C001_A1", synthetic_actor_id="procurement_01", **planned_step_kwargs),
                PlannedStep(planned_step_id="C001_A3", synthetic_actor_id="warehouse_01", **planned_step_kwargs),
            ],
        )


def test_human_delay_profile_omits_missing_actor_criteria() -> None:
    criteria = CompiledRealismCriteria(
        actor_criteria={},
        demand_releases=[],
        criteria_hash="criteria",
        llm_metadata={},
        actor_day_profiles={},
        price_anchors={},
        material_demand_profiles={},
        demand_patterns=[],
    )

    assert _human_delay_profile("missing_actor", criteria) == {}


def test_manifest_actor_projection_uses_planned_actor_session_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    case = CasePlan(
        case_id="C001",
        process_type="procure_to_pay",
        material_id="MA025",
        vendor_id="V17121",
        plant="MI00",
        purchasing_org="US00",
        storage_location="0002",
        storage_location_label="Trading Goods",
        quantity=10,
        target_price=20.0,
        currency="USD",
        delivery_date=date(2026, 5, 18),
        gross_amount=200.0,
    )
    planned_step = PlannedStep(
        planned_step_id="C001_A1",
        case_id="C001",
        step_id="A1",
        step_type="create_purchase_requisition",
        tool_name="fiori.create_purchase_requisition",
        synthetic_actor_id="procurement_01",
        technical_sap_user_id="GBGEN_P01",
        actor_session_id="custom-procurement-session",
        inputs={},
        required_sap_object_keys=[],
        planned_date_inputs={},
        target_start=datetime(2026, 5, 18, 8, 0),
        target_end=datetime(2026, 5, 18, 8, 1),
    )

    manifest = _post_processing_manifest(config, [case], [planned_step], "RUN_TEST", "config-hash")

    assert manifest["realism_criteria_hash"] is None
    assert manifest["actor_projection"] == [
        {
            "synthetic_actor_id": "procurement_01",
            "technical_sap_user_id": "GBGEN_P01",
            "actor_session_id": "custom-procurement-session",
            "expose_as": "procurement_01",
        }
    ]


def test_scheduler_assigns_configured_multi_step_actor_without_overlap(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)

    planned_steps = plan_steps(config, plan_cases(config, Random(17)), Random(17))
    procurement_nodes = [
        planned_step for planned_step in planned_steps
        if planned_step.synthetic_actor_id == "procurement_01"
    ]

    assert {planned_step.step_type for planned_step in procurement_nodes} == {
        "create_purchase_requisition",
        "create_purchase_order",
    }
    _assert_no_resource_overlap(procurement_nodes)


def test_scheduler_uses_second_capable_actor_when_first_is_busy(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"].insert(
        1,
        {
            **_actor("procurement_02", "procurement", "SAP_USER_4"),
            "delayMultiplier": 1.0,
        },
    )
    payload["technicalUsers"].append(_technical_user("GBGEN_P04", "SAP_USER_4"))
    payload["identityMappings"].append({"syntheticActorId": "procurement_02", "technicalSapUserId": "GBGEN_P04"})
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    tz = ZoneInfo(config.run_settings.target_timezone)
    demand_releases = [
        DemandRelease("C001", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
        DemandRelease("C002", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
    ]

    planned_steps = plan_steps(config, plan_cases(config, Random(17), demand_releases=demand_releases), Random(17))
    requisition_actors = {
        planned_step.synthetic_actor_id
        for planned_step in planned_steps
        if planned_step.step_type == "create_purchase_requisition"
    }

    assert requisition_actors == {"procurement_01", "procurement_02"}


def test_scheduler_orders_ready_steps_on_global_company_timeline(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 3
    payload["runSettings"]["runHorizonDays"] = 10
    payload["runSettings"]["interStepDelayMinutes"][0]["min"] = 60
    payload["runSettings"]["interStepDelayMinutes"][0]["max"] = 60
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    tz = ZoneInfo(config.run_settings.target_timezone)
    demand_releases = [
        DemandRelease("C001", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
        DemandRelease("C002", datetime(2026, 5, 18, 8, 10, tzinfo=tz), "MA025"),
        DemandRelease("C003", datetime(2026, 5, 18, 8, 20, tzinfo=tz), "MA025"),
    ]

    planned_steps = plan_steps(
        config,
        plan_cases(config, Random(17), demand_releases=demand_releases),
        Random(17),
        actor_criteria=_actor_criteria(config),
    )
    ordered = sorted(planned_steps, key=lambda planned_step: planned_step.target_start)

    assert [(step.case_id, step.step_type) for step in ordered[:4]] == [
        ("C001", "create_purchase_requisition"),
        ("C002", "create_purchase_requisition"),
        ("C003", "create_purchase_requisition"),
        ("C001", "create_purchase_order"),
    ]


def test_scheduler_applies_hard_delivery_date_gate_to_goods_receipt(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["runHorizonDays"] = 10
    payload["masterData"][0]["deliveryLeadTimeMinDays"] = 5
    payload["masterData"][0]["deliveryLeadTimeMaxDays"] = 5
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    tz = ZoneInfo(config.run_settings.target_timezone)
    demand_releases = [
        DemandRelease("C001", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
        DemandRelease("C002", datetime(2026, 5, 18, 8, 30, tzinfo=tz), "MA025"),
    ]

    cases = plan_cases(config, Random(17), demand_releases=demand_releases)
    planned_steps = plan_steps(config, cases, Random(17), actor_criteria=_actor_criteria(config))

    goods_receipts = [step for step in planned_steps if step.step_type == "post_goods_receipt"]
    delivery_date_by_case = {case.case_id: case.delivery_date for case in cases}
    assert goods_receipts
    for step in goods_receipts:
        assert step.target_start.date() >= delivery_date_by_case[step.case_id]


def test_scheduler_respects_material_valuation_lock_buffer(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 2
    payload["runSettings"]["runHorizonDays"] = 10
    payload["runSettings"]["maxParallelActorSessions"] = 4
    payload["runSettings"]["realism"] = {"materialValuationLockBufferSeconds": 120}
    payload["runSettings"]["stepDurationMinutes"]["post_goods_receipt"] = {"min": 1, "max": 1}
    payload["actors"].insert(2, _actor("warehouse_02", "warehouse", "SAP_USER_4"))
    payload["technicalUsers"].append(_technical_user("GBGEN_P04", "SAP_USER_4"))
    payload["identityMappings"].append({"syntheticActorId": "warehouse_02", "technicalSapUserId": "GBGEN_P04"})
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    tz = ZoneInfo(config.run_settings.target_timezone)
    demand_releases = [
        DemandRelease("C001", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
        DemandRelease("C002", datetime(2026, 5, 18, 8, 0, tzinfo=tz), "MA025"),
    ]

    planned_steps = plan_steps(
        config,
        plan_cases(config, Random(17), demand_releases=demand_releases),
        Random(17),
        actor_criteria=_actor_criteria(config),
    )
    lock_steps = sorted(
        [
            step
            for step in planned_steps
            if step.labels.get("material_valuation_lock_key") == "MI00:MA025"
        ],
        key=lambda item: item.target_start,
    )

    assert lock_steps
    for first, second in zip(lock_steps, lock_steps[1:]):
        assert second.target_start >= first.target_end + timedelta(seconds=120)


def test_scheduler_respects_shared_technical_user_availability(tmp_path: Path) -> None:
    payload = _base_config()
    for mapping in payload["identityMappings"]:
        mapping["technicalSapUserId"] = "GBGEN_P01"
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)

    planned_steps = plan_steps(config, plan_cases(config, Random(17)), Random(17))

    _assert_no_resource_overlap(planned_steps)


def test_wave_scheduler_prevents_shared_technical_user_in_same_wave(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"].insert(
        1,
        {
            **_actor("procurement_02", "procurement", "SAP_USER_4"),
            "delayMultiplier": 1.0,
        },
    )
    payload["identityMappings"].append({"syntheticActorId": "procurement_02", "technicalSapUserId": "GBGEN_P01"})
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    planned_steps = plan_steps(config, plan_cases(config, Random(17)), Random(17))
    planned_steps_by_id = {planned_step.planned_step_id: planned_step for planned_step in planned_steps}

    for wave in plan_waves(config, planned_steps):
        technical_user_ids = [
            planned_steps_by_id[item["planned_step_id"]].technical_sap_user_id
            for item in wave["planned_steps"]
        ]
        assert len(technical_user_ids) == len(set(technical_user_ids))


def test_wave_scheduler_prevents_same_material_lock_key_in_same_wave(tmp_path: Path) -> None:
    payload = _base_config()
    payload["actors"].insert(1, _actor("procurement_02", "procurement", "SAP_USER_4"))
    payload["technicalUsers"].append(_technical_user("GBGEN_P04", "SAP_USER_4"))
    payload["identityMappings"].append({"syntheticActorId": "procurement_02", "technicalSapUserId": "GBGEN_P04"})
    payload["runSettings"]["maxParallelActorSessions"] = 2
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    planned_steps = [
        PlannedStep(
            planned_step_id="C001_A1",
            case_id="C001",
            step_id="A1",
            step_type="create_purchase_requisition",
            tool_name="fiori.create_purchase_requisition",
            synthetic_actor_id="procurement_01",
            technical_sap_user_id="GBGEN_P01",
            actor_session_id="procurement_01-session",
            inputs={},
            required_sap_object_keys=[],
            planned_date_inputs={},
            target_start=datetime(2026, 5, 18, 8, 0),
            target_end=datetime(2026, 5, 18, 8, 1),
            labels={"step_label": "normal", "material_valuation_lock_key": "MI00:MA025"},
        ),
        PlannedStep(
            planned_step_id="C002_A1",
            case_id="C002",
            step_id="A1",
            step_type="create_purchase_requisition",
            tool_name="fiori.create_purchase_requisition",
            synthetic_actor_id="procurement_02",
            technical_sap_user_id="GBGEN_P04",
            actor_session_id="procurement_02-session",
            inputs={},
            required_sap_object_keys=[],
            planned_date_inputs={},
            target_start=datetime(2026, 5, 18, 8, 0),
            target_end=datetime(2026, 5, 18, 8, 1),
            labels={"step_label": "normal", "material_valuation_lock_key": "MI00:MA025"},
        ),
    ]

    waves = plan_waves(config, planned_steps)

    assert [len(wave["planned_steps"]) for wave in waves[:2]] == [1, 1]


def test_plan_cases_excludes_blocked_materials_without_release_material(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 4
    payload["runSettings"]["realism"] = {"blockedMaterials": ["MA025"]}
    payload["masterData"].append({**payload["masterData"][0], "materialId": "MB026"})
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)

    cases = plan_cases(config, Random(17))

    assert {case.material_id for case in cases} == {"MB026"}


def test_config_deduplicates_blocked_materials_before_all_materials_check(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {"blockedMaterials": ["MA025", "MA025"]}
    payload["masterData"].append({**payload["masterData"][0], "materialId": "MB026"})
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    config = load_generation_config(config_path)

    assert config.run_settings.realism.blocked_materials == ("MA025", "MA025")


def test_wave_scheduler_waits_for_all_dependency_parents(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["dependencies"] = [
        _dependency("create_purchase_requisition", "post_goods_receipt"),
        _dependency("create_purchase_order", "post_goods_receipt"),
    ]
    payload["runSettings"]["maxParallelActorSessions"] = 2
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    planned_steps = [
        PlannedStep(
            planned_step_id="C001_A2",
            case_id="C001",
            step_id="A2",
            step_type="create_purchase_order",
            tool_name="fiori.create_purchase_order",
            synthetic_actor_id="procurement_01",
            technical_sap_user_id="GBGEN_P01",
            actor_session_id="procurement_01-session",
            inputs={},
            required_sap_object_keys=[],
            planned_date_inputs={},
            target_start=datetime(2026, 5, 18, 8, 0),
            target_end=datetime(2026, 5, 18, 8, 1),
        ),
        PlannedStep(
            planned_step_id="C001_A3",
            case_id="C001",
            step_id="A3",
            step_type="post_goods_receipt",
            tool_name="fiori.create_goods_receipt",
            synthetic_actor_id="warehouse_01",
            technical_sap_user_id="GBGEN_P02",
            actor_session_id="warehouse_01-session",
            inputs={},
            required_sap_object_keys=[],
            planned_date_inputs={},
            target_start=datetime(2026, 5, 18, 8, 1),
            target_end=datetime(2026, 5, 18, 8, 2),
        ),
        PlannedStep(
            planned_step_id="C001_A1",
            case_id="C001",
            step_id="A1",
            step_type="create_purchase_requisition",
            tool_name="fiori.create_purchase_requisition",
            synthetic_actor_id="procurement_01",
            technical_sap_user_id="GBGEN_P01",
            actor_session_id="procurement_01-session",
            inputs={},
            required_sap_object_keys=[],
            planned_date_inputs={},
            target_start=datetime(2026, 5, 18, 9, 0),
            target_end=datetime(2026, 5, 18, 9, 1),
        ),
    ]

    waves = plan_waves(config, planned_steps)
    wave_by_step = {
        item["planned_step_id"]: wave["sequence_no"]
        for wave in waves
        for item in wave["planned_steps"]
    }

    assert wave_by_step["C001_A3"] > wave_by_step["C001_A1"]
    assert wave_by_step["C001_A3"] > wave_by_step["C001_A2"]


def _assert_no_resource_overlap(planned_steps: list[PlannedStep]) -> None:
    for first, second in zip(
        sorted(planned_steps, key=lambda planned_step: planned_step.target_start),
        sorted(planned_steps, key=lambda planned_step: planned_step.target_start)[1:],
    ):
        assert first.target_end <= second.target_start


def _actor_criteria(config) -> dict[str, ActorRealismCriteria]:
    return {
        actor.id: ActorRealismCriteria(
            actor_id=actor.id,
            delay_multiplier=actor.delay_multiplier,
            workday_deviation_hours=0.0,
            pause_duration_minutes=30,
        )
        for actor in config.actors
    }


def test_generated_inputs_fail_for_unknown_executor_tool(tmp_path: Path) -> None:
    payload = _base_config()
    unknown_tool = _tool(
        "fiori.unknown_tool",
        [
            "material",
            "quantity",
            "valuation_price",
            "currency",
            "price_unit",
            "delivery_date",
            "plant",
            "purchasing_group",
            "purchasing_organization",
            "company_code",
        ],
    )
    payload["toolRequirements"]["fiori.unknown_tool"] = unknown_tool
    payload["processes"][0]["steps"][0]["tool"] = unknown_tool
    config_path = tmp_path / "main.yaml"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="Tool 'fiori.unknown_tool' is not registered"):
        generate_trace_artifacts(
            config_path=config_path,
            out_dir=out_dir,
            run_id="RUN_UNKNOWN_TOOL",
            seed=17,
        )


def test_timeline_reuses_sampled_boundaries_per_day(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["workingHours"]["dailyDeviationHoursMin"] = -1.0
    payload["runSettings"]["workingHours"]["dailyDeviationHoursMax"] = 1.0
    payload["runSettings"]["workingHours"]["pauseDurationMinutesMin"] = 30
    payload["runSettings"]["workingHours"]["pauseDurationMinutesMax"] = 75
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    planner = TimelinePlanner(config.run_settings, Random(17))

    first = planner._boundaries_for(config.run_settings.run_start_date)
    second = planner._boundaries_for(config.run_settings.run_start_date)

    assert first == second


def test_timeline_rejects_non_positive_delay_multiplier(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    planner = TimelinePlanner(config.run_settings, Random(17))

    with pytest.raises(TraceGenerationError, match="delay_multiplier must be greater than 0"):
        planner.add_step_duration(planner.first_start(), "create_purchase_requisition", 0)


def test_timeline_carries_remaining_duration_across_pause_and_workday(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["stepDurationMinutes"]["create_purchase_requisition"] = {"min": 20, "max": 20}
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)
    config = load_generation_config(config_path)
    planner = TimelinePlanner(config.run_settings, Random(17))

    pause_crossing_start = planner.first_start().replace(hour=11, minute=50)
    pause_crossing_end = planner.add_step_duration(pause_crossing_start, "create_purchase_requisition", 1.0)
    day_crossing_start = planner.first_start().replace(hour=16, minute=50)
    day_crossing_end = planner.add_step_duration(day_crossing_start, "create_purchase_requisition", 1.0)

    assert pause_crossing_end == pause_crossing_start.replace(hour=12, minute=40)
    assert day_crossing_end == (day_crossing_start + timedelta(days=1)).replace(hour=8, minute=10)


def test_generated_inputs_validate_against_current_tool_schemas(tmp_path: Path) -> None:
    payload = _base_config()
    price_unit = next(
        binding
        for binding in payload["processes"][0]["steps"][0]["inputBindings"]
        if binding["field"] == "price_unit"
    )
    price_unit["value"] = "0"
    config_path = tmp_path / "main.yaml"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="Invalid input for tool 'fiori.create_purchase_requisition'"):
        generate_trace_artifacts(
            config_path=config_path,
            out_dir=out_dir,
            run_id="RUN_BAD_INPUT",
            seed=17,
        )


def test_generation_emits_canonical_trace_and_post_processing_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, _base_config())

    artifacts = generate_trace_artifacts(
        config_path=config_path,
        out_dir=out_dir,
        run_id="RUN_TEST_001",
        seed=17,
    )

    assert artifacts.execution_trace_path.name == "RUN_TEST_001.execution-trace.yaml"
    assert artifacts.post_processing_manifest_path.name == "RUN_TEST_001.post-processing-manifest.yaml"
    assert not (out_dir / "RUN_TEST_001.executor.trace.jsonl").exists()

    execution_trace = yaml.safe_load(artifacts.execution_trace_path.read_text(encoding="utf-8"))
    assert execution_trace["trace_version"] == "0.3"
    assert execution_trace["run_id"] == "RUN_TEST_001"
    assert execution_trace["realism_criteria_hash"] == execution_trace["llm_metadata"]["realism_criteria_hash"]
    assert execution_trace["actor_sessions"] == [
        {
            "actor_session_id": "procurement_01-session",
            "synthetic_actor_id": "procurement_01",
            "technical_sap_user_id": "GBGEN_P01",
                "username_env_var": "SAP_USER_1_UN",
                "password_env_var": "SAP_USER_1_PW",
                "login_url_env_var": "SAP_URL",
                "human_delay_profile": {"delay_multiplier": 1.0},
            },
            {
                "actor_session_id": "warehouse_01-session",
                "synthetic_actor_id": "warehouse_01",
                "technical_sap_user_id": "GBGEN_P02",
                "username_env_var": "SAP_USER_2_UN",
                "password_env_var": "SAP_USER_2_PW",
                "login_url_env_var": "SAP_URL",
                "human_delay_profile": {"delay_multiplier": 1.0},
            },
            {
                "actor_session_id": "accounts_payable_01-session",
                "synthetic_actor_id": "accounts_payable_01",
                "technical_sap_user_id": "GBGEN_P03",
                "username_env_var": "SAP_USER_3_UN",
                "password_env_var": "SAP_USER_3_PW",
                "login_url_env_var": "SAP_URL",
                "human_delay_profile": {"delay_multiplier": 1.0},
            },
        ]
    assert "secret" not in json.dumps(execution_trace)
    assert [step["step_type"] for step in execution_trace["dependency_graph"]["planned_steps"][:2]] == [
        "create_purchase_requisition",
        "create_purchase_requisition",
    ]
    c001_steps = [
        step["step_type"]
        for step in execution_trace["dependency_graph"]["planned_steps"]
        if step["case_id"] == "C001"
    ]
    assert c001_steps == [
        "create_purchase_requisition",
        "create_purchase_order",
        "post_goods_receipt",
        "enter_incoming_invoice",
        "post_outgoing_payment",
    ]
    purchase_order_node = next(
        step
        for step in execution_trace["dependency_graph"]["planned_steps"]
        if step["case_id"] == "C001" and step["step_type"] == "create_purchase_order"
    )
    assert purchase_order_node["inputs"] == {
        "purchase_requisition": "$purchase_requisition.pr_number",
        "storage_location": "0002",
        "supplier": "V17121",
        "quantity": 10,
        "net_price": execution_trace["cases"][0]["line_items"][0]["target_price"],
    }
    goods_receipt_node = next(
        step
        for step in execution_trace["dependency_graph"]["planned_steps"]
        if step["case_id"] == "C001" and step["step_type"] == "post_goods_receipt"
    )
    assert goods_receipt_node["inputs"] == {
        "purchase_order": "$purchase_order.po_number",
        "storage_location": "Trading Goods",
    }
    assert goods_receipt_node["planned_date_inputs"] == {
        "document_date": "2026-05-23",
        "posting_date": "2026-05-23",
    }
    invoice_node = next(
        step
        for step in execution_trace["dependency_graph"]["planned_steps"]
        if step["case_id"] == "C001" and step["step_type"] == "enter_incoming_invoice"
    )
    assert invoice_node["inputs"] == {
        "gross_amount": 200.0,
        "purchase_order": "$purchase_order.po_number",
        "tax_code": "XI",
    }
    assert invoice_node["planned_date_inputs"] == {
        "invoice_date": "2026-05-23",
    }
    assert execution_trace["execution_schedule"]["mode"] == "waves"
    assert execution_trace["execution_schedule"]["waves"][0]["planned_steps"][0]["planned_step_id"] == "C001_A1"
    assert execution_trace["validation_report"]["errors"] == []

    manifest = yaml.safe_load(artifacts.post_processing_manifest_path.read_text(encoding="utf-8"))
    ExecutionTraceArtifact.model_validate(execution_trace)
    PostProcessingManifestArtifact.model_validate(manifest)
    assert manifest["run_id"] == "RUN_TEST_001"
    assert manifest["realism_criteria_hash"] == execution_trace["realism_criteria_hash"]
    assert manifest["timestamp_policy"]["source"] == "planned_synthetic_time"
    assert [item["id"] for item in manifest["post_processing_exports"]] == [
        "change_documents",
        "purchase_orders",
        "material_documents",
        "supplier_invoices",
        "accounting_documents",
    ]
    assert manifest["actor_projection"][0] == {
        "synthetic_actor_id": "procurement_01",
        "technical_sap_user_id": "GBGEN_P01",
        "actor_session_id": "procurement_01-session",
        "expose_as": "procurement_01",
    }
    assert manifest["object_lineage"][0]["chain"] == [
        "purchase_requisition",
        "purchase_order",
        "material_document",
        "supplier_invoice",
        "payment_document",
    ]
    assert {
        (item["planned_step_id"], item["field"], item["planned_value"], item["runtime_value_policy"])
        for item in manifest["planned_date_input_overrides"]
    } == {
        ("C001_A3", "document_date", "2026-05-23", "sap_current_date"),
        ("C001_A3", "posting_date", "2026-05-23", "sap_current_date"),
        ("C001_A4", "invoice_date", "2026-05-23", "executor_current_date"),
        ("C002_A3", "document_date", "2026-05-23", "sap_current_date"),
        ("C002_A3", "posting_date", "2026-05-23", "sap_current_date"),
        ("C002_A4", "invoice_date", "2026-05-23", "executor_current_date"),
    }
    assert {item["step_type"] for item in manifest["planned_date_input_overrides"]} == {
        "post_goods_receipt",
        "enter_incoming_invoice",
    }

    first_start = datetime.fromisoformat(execution_trace["dependency_graph"]["planned_steps"][0]["planned_synthetic_time"]["start"])
    second_start = datetime.fromisoformat(execution_trace["dependency_graph"]["planned_steps"][1]["planned_synthetic_time"]["start"])
    assert (second_start - first_start).total_seconds() >= 30 * 60


def test_committed_artifact_json_schemas_are_current() -> None:
    execution_schema_path, manifest_schema_path = schema_output_paths()

    assert execution_schema_path.exists()
    assert manifest_schema_path.exists()
    assert json.loads(execution_schema_path.read_text(encoding="utf-8")) == ExecutionTraceArtifact.model_json_schema()
    assert json.loads(manifest_schema_path.read_text(encoding="utf-8")) == PostProcessingManifestArtifact.model_json_schema()


def test_cli_writes_artifacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "main.yaml"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, _base_config())

    exit_code = main(
        [
            str(config_path),
            "--out-dir",
            str(out_dir),
            "--run-id",
            "RUN_TEST_002",
            "--seed",
            "19",
        ]
    )

    assert exit_code == 0
    assert (out_dir / "RUN_TEST_002.execution-trace.yaml").exists()
    assert not (out_dir / "RUN_TEST_002.executor.trace.jsonl").exists()
    assert (out_dir / "RUN_TEST_002.post-processing-manifest.yaml").exists()
    output = capsys.readouterr().out
    assert "RUN_TEST_002.execution-trace.yaml" in output
    assert "RUN_TEST_002.executor.trace.jsonl" not in output
