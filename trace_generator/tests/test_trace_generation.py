from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from random import Random

import pytest
import yaml

from erp_trace_generator.artifact_models import ExecutionTraceArtifact, PostProcessingManifestArtifact
from erp_trace_generator.artifacts import _session_records
from erp_trace_generator.bindings import resolve_step_inputs
from erp_trace_generator.cli import main
from erp_trace_generator.config import load_generation_config
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.fraud import FRAUD_TRANSFORMERS, register_fraud_transformer
from erp_trace_generator.generator import generate_trace_artifacts
from erp_trace_generator.models import CasePlan, InputBinding, PlannedNode, ProcessStep
from erp_trace_generator.schema_export import schema_output_paths
from erp_trace_generator.timeline import TimelinePlanner


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_env(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "SAP_URL=https://sap.example.test/flp",
                "SAP_USER_1_UN=BUYER1",
                "SAP_USER_1_PW=secret1",
                "SAP_USER_2_UN=WAREHOUSE1",
                "SAP_USER_2_PW=secret2",
                "SAP_USER_3_UN=AP1",
                "SAP_USER_3_PW=secret3",
            ]
        ),
        encoding="utf-8",
    )


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
            {"virtualActorId": "procurement_01", "technicalUserId": "GBGEN_P01"},
            {"virtualActorId": "warehouse_01", "technicalUserId": "GBGEN_P02"},
            {"virtualActorId": "accounts_payable_01", "technicalUserId": "GBGEN_P03"},
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
                    _step("A1", "create_purchase_requisition", "fiori.create_purchase_requisition", "procurement"),
                    _step("A2", "create_purchase_order", "fiori.create_purchase_order", "procurement"),
                    _step("A3", "post_goods_receipt", "fiori.create_goods_receipt", "warehouse"),
                    _step("A4", "enter_incoming_invoice", "fiori.create_supplier_invoice", "accounts_payable"),
                    _step("A5", "post_outgoing_payment", "fiori.send_payment", "accounts_payable"),
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
            "maxParallelSessions": 2,
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
                ["purchase_requisition", "storage_location", "supplier", "quantity"],
            ),
            "fiori.create_goods_receipt": _tool(
                "fiori.create_goods_receipt",
                ["purchase_order", "document_date", "posting_date", "storage_location"],
            ),
            "fiori.create_supplier_invoice": _tool(
                "fiori.create_supplier_invoice",
                ["invoice_date", "invoicing_party", "gross_amount", "purchase_order"],
            ),
            "fiori.send_payment": _tool(
                "fiori.send_payment",
                ["company_code", "posting_document_date", "supplier", "accounting_document", "general_ledger_account", "amount"],
            ),
        },
    }


def _actor(actor_id: str, role: str, user_prefix: str) -> dict:
    return {
        "id": actor_id,
        "displayName": actor_id,
        "role": role,
        "timezone": "Europe/Berlin",
        "workLocation": "HD00",
        "speedFactor": 1.0,
        "realismProfile": {
            "workerType": role,
            "workingHoursDeviation": 0.0,
            "pauseCharacteristicsIndex": 10,
        },
        "exposeInFinalDatasetAs": actor_id,
    }


def _technical_user(user_id: str, env_prefix: str) -> dict:
    return {
        "id": user_id,
        "usernameEnvVar": f"{env_prefix}_UN",
        "passwordEnvVar": f"{env_prefix}_PW",
        "loginUrlEnvVar": "SAP_URL",
        "maxConcurrentSessions": 1,
    }


def _tool(name: str, required_fields: list[str]) -> dict:
    return {
        "toolName": name,
        "title": name.rsplit(".", 1)[-1].replace("_", " ").title(),
        "inputModel": name.rsplit(".", 1)[-1].title().replace("_", "") + "Input",
        "requiredInputFields": required_fields,
        "inputProperties": [{"name": field, "schemaType": "string", "required": True} for field in required_fields],
    }


def _step(step_id: str, step_type: str, tool_name: str, role: str) -> dict:
    return {
        "stepId": step_id,
        "stepType": step_type,
        "tool": {"toolName": tool_name, "title": tool_name, "inputModel": "Input", "requiredInputFields": [], "inputProperties": []},
        "requiredRole": role,
        "inputBindings": _input_bindings(step_type),
        "expectedOutputs": _expected_outputs(step_type),
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
        ],
        "post_goods_receipt": [
            _binding("purchase_order", "prior_output", "purchase_order.po_number"),
            _binding("document_date", "derived", "fiori_delivery_date"),
            _binding("posting_date", "derived", "fiori_delivery_date"),
            _binding("storage_location", "derived", "storage_location_label"),
        ],
        "enter_incoming_invoice": [
            _binding("invoice_date", "derived", "fiori_delivery_date"),
            _binding("invoicing_party", "master_data", "vendor_id"),
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


def _binding(field: str, source: str, value: str, value_type: str = "string") -> dict:
    return {"field": field, "source": source, "value": value, "valueType": value_type}


def _expected_outputs(step_type: str) -> list[str]:
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
        required_role="procurement",
        input_bindings=(
            InputBinding("sample_step", "material", "master_data", "materialId"),
            InputBinding("sample_step", "quantity", "case", "quantity"),
            InputBinding("sample_step", "posting_date", "business_date", "delivery_date"),
            InputBinding("sample_step", "purchase_order", "prior_output", "purchase_order.po_number"),
            InputBinding("sample_step", "price_unit", "literal", "1", "int"),
            InputBinding("sample_step", "amount", "derived", "gross_amount"),
            InputBinding("sample_step", "document_date", "derived", "fiori_delivery_date"),
            InputBinding("sample_step", "storage_location", "derived", "storage_location_label"),
        ),
        expected_outputs=("sample.output",),
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
        required_role="procurement",
        input_bindings=(InputBinding("sample_step", "enabled", "literal", "maybe", "bool"),),
    )

    with pytest.raises(TraceGenerationError, match="Cannot cast literal 'maybe' to bool"):
        resolve_step_inputs(step, case)


def test_session_records_reject_same_session_for_multiple_actors(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    node_kwargs = {
        "case_id": "C001",
        "step_id": "A1",
        "step_type": "create_purchase_requisition",
        "tool_name": "fiori.create_purchase_requisition",
        "technical_user_id": "GBGEN_P01",
        "session_id": "shared-session",
        "inputs": {},
        "expected_outputs": [],
        "business_dates": {},
        "target_start": datetime(2026, 5, 18, 8, 0),
        "target_end": datetime(2026, 5, 18, 8, 1),
    }

    with pytest.raises(TraceGenerationError, match="shared-session"):
        _session_records(
            config,
            [
                PlannedNode(node_id="C001_A1", virtual_actor_id="procurement_01", **node_kwargs),
                PlannedNode(node_id="C001_A3", virtual_actor_id="warehouse_01", **node_kwargs),
            ],
        )


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
    env_path = tmp_path / ".env"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, payload)
    _write_env(env_path)

    with pytest.raises(TraceGenerationError, match="Tool 'fiori.unknown_tool' is not registered"):
        generate_trace_artifacts(
            config_path=config_path,
            env_path=env_path,
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


def test_timeline_rejects_non_positive_speed_factor(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, _base_config())
    config = load_generation_config(config_path)
    planner = TimelinePlanner(config.run_settings, Random(17))

    with pytest.raises(TraceGenerationError, match="speed_factor must be greater than 0"):
        planner.add_step_duration(planner.first_start(), "create_purchase_requisition", 0)


def test_generated_inputs_validate_against_current_tool_schemas(tmp_path: Path) -> None:
    payload = _base_config()
    price_unit = next(
        binding
        for binding in payload["processes"][0]["steps"][0]["inputBindings"]
        if binding["field"] == "price_unit"
    )
    price_unit["value"] = "0"
    config_path = tmp_path / "main.yaml"
    env_path = tmp_path / ".env"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, payload)
    _write_env(env_path)

    with pytest.raises(TraceGenerationError, match="Invalid input for tool 'fiori.create_purchase_requisition'"):
        generate_trace_artifacts(
            config_path=config_path,
            env_path=env_path,
            out_dir=out_dir,
            run_id="RUN_BAD_INPUT",
            seed=17,
        )


def test_generation_emits_canonical_trace_jsonl_and_post_processing_manifest(tmp_path: Path) -> None:
    config_path = tmp_path / "main.yaml"
    env_path = tmp_path / ".env"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, _base_config())
    _write_env(env_path)

    artifacts = generate_trace_artifacts(
        config_path=config_path,
        env_path=env_path,
        out_dir=out_dir,
        run_id="RUN_TEST_001",
        seed=17,
    )

    assert artifacts.execution_trace_path.name == "RUN_TEST_001.execution-trace.yaml"
    assert artifacts.executor_trace_path.name == "RUN_TEST_001.executor.trace.jsonl"
    assert artifacts.post_processing_manifest_path.name == "RUN_TEST_001.post-processing-manifest.yaml"

    execution_trace = yaml.safe_load(artifacts.execution_trace_path.read_text(encoding="utf-8"))
    assert execution_trace["trace_version"] == "0.1"
    assert execution_trace["run_id"] == "RUN_TEST_001"
    assert execution_trace["sessions"] == [
        {
            "session_id": "procurement_01-session",
            "virtual_actor_id": "procurement_01",
            "technical_user_id": "GBGEN_P01",
            "username_env_var": "SAP_USER_1_UN",
            "password_env_var": "SAP_USER_1_PW",
            "login_url_env_var": "SAP_URL",
        },
        {
            "session_id": "warehouse_01-session",
            "virtual_actor_id": "warehouse_01",
            "technical_user_id": "GBGEN_P02",
            "username_env_var": "SAP_USER_2_UN",
            "password_env_var": "SAP_USER_2_PW",
            "login_url_env_var": "SAP_URL",
        },
        {
            "session_id": "accounts_payable_01-session",
            "virtual_actor_id": "accounts_payable_01",
            "technical_user_id": "GBGEN_P03",
            "username_env_var": "SAP_USER_3_UN",
            "password_env_var": "SAP_USER_3_PW",
            "login_url_env_var": "SAP_URL",
        },
    ]
    assert "secret" not in json.dumps(execution_trace)
    assert [step["step_type"] for step in execution_trace["dependency_graph"]["nodes"][:5]] == [
        "create_purchase_requisition",
        "create_purchase_order",
        "post_goods_receipt",
        "enter_incoming_invoice",
        "post_outgoing_payment",
    ]
    assert execution_trace["execution_schedule"]["mode"] == "waves"
    assert execution_trace["execution_schedule"]["waves"][0]["nodes"][0]["node_id"] == "C001_A1"
    assert execution_trace["validation_report"]["errors"] == []

    jsonl_records = [
        json.loads(line)
        for line in artifacts.executor_trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scheduled_node_ids = [
        item["node_id"]
        for wave in execution_trace["execution_schedule"]["waves"]
        for item in sorted(wave["nodes"], key=lambda value: value["startup_order"])
    ]
    assert [record["task_id"] for record in jsonl_records[1:]] == scheduled_node_ids
    assert jsonl_records[0] == {
        "kind": "init",
        "users": [
            {"session_id": "procurement_01-session", "user_id": "procurement_01", "username": "BUYER1", "login_url": "https://sap.example.test/flp"},
            {"session_id": "warehouse_01-session", "user_id": "warehouse_01", "username": "WAREHOUSE1", "login_url": "https://sap.example.test/flp"},
            {"session_id": "accounts_payable_01-session", "user_id": "accounts_payable_01", "username": "AP1", "login_url": "https://sap.example.test/flp"},
        ],
    }
    assert jsonl_records[1]["task_id"] == "C001_A1"
    records_by_task_id = {record["task_id"]: record for record in jsonl_records[1:]}
    assert records_by_task_id["C001_A2"]["input"]["purchase_requisition"] == "$purchase_requisition.pr_number"
    assert records_by_task_id["C001_A3"]["input"]["purchase_order"] == "$purchase_order.po_number"
    assert records_by_task_id["C001_A5"]["input"]["accounting_document"] == "$supplier_invoice.invoice_number"
    assert "password" not in json.dumps(jsonl_records)
    task_starts = [
        datetime.fromisoformat(record["meta"]["target_synthetic_start"])
        for record in jsonl_records[1:]
    ]
    assert task_starts == sorted(task_starts)

    manifest = yaml.safe_load(artifacts.post_processing_manifest_path.read_text(encoding="utf-8"))
    ExecutionTraceArtifact.model_validate(execution_trace)
    PostProcessingManifestArtifact.model_validate(manifest)
    assert manifest["run_id"] == "RUN_TEST_001"
    assert manifest["timestamp_policy"]["source"] == "planned_target_synthetic_time"
    assert [item["id"] for item in manifest["post_processing_exports"]] == [
        "change_documents",
        "purchase_orders",
        "material_documents",
        "supplier_invoices",
        "accounting_documents",
    ]
    assert manifest["actor_projection"][0] == {
        "virtual_actor_id": "procurement_01",
        "technical_user_id": "GBGEN_P01",
        "session_id": "procurement_01-session",
        "expose_as": "procurement_01",
    }
    assert manifest["object_lineage"][0]["chain"] == [
        "purchase_requisition",
        "purchase_order",
        "material_document",
        "supplier_invoice",
        "payment_document",
    ]

    first_start = datetime.fromisoformat(execution_trace["dependency_graph"]["nodes"][0]["target_synthetic_time"]["start"])
    second_start = datetime.fromisoformat(execution_trace["dependency_graph"]["nodes"][1]["target_synthetic_time"]["start"])
    assert (second_start - first_start).total_seconds() >= 30 * 60


def test_committed_artifact_json_schemas_are_current() -> None:
    execution_schema_path, manifest_schema_path = schema_output_paths()

    assert execution_schema_path.exists()
    assert manifest_schema_path.exists()
    assert json.loads(execution_schema_path.read_text(encoding="utf-8")) == ExecutionTraceArtifact.model_json_schema()
    assert json.loads(manifest_schema_path.read_text(encoding="utf-8")) == PostProcessingManifestArtifact.model_json_schema()


def test_cli_writes_artifacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "main.yaml"
    env_path = tmp_path / ".env"
    out_dir = tmp_path / "build"
    _write_yaml(config_path, _base_config())
    _write_env(env_path)

    exit_code = main(
        [
            str(config_path),
            "--env-file",
            str(env_path),
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
    assert (out_dir / "RUN_TEST_002.executor.trace.jsonl").exists()
    assert (out_dir / "RUN_TEST_002.post-processing-manifest.yaml").exists()
    assert "RUN_TEST_002.executor.trace.jsonl" in capsys.readouterr().out
