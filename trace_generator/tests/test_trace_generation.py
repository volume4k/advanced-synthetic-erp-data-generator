from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from erp_trace_generator.cli import main
from erp_trace_generator.config import load_generation_config
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.generator import generate_trace_artifacts


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
    }


def _dependency(from_step_type: str, to_step_type: str) -> dict:
    return {
        "fromStepType": from_step_type,
        "toStepType": to_step_type,
        "description": f"{from_step_type} before {to_step_type}",
    }


def test_config_loader_rejects_active_null_tool(tmp_path: Path) -> None:
    payload = _base_config()
    payload["processes"][0]["steps"][2]["tool"] = None
    config_path = tmp_path / "main.yaml"
    _write_yaml(config_path, payload)

    with pytest.raises(TraceGenerationError, match="has no tool"):
        load_generation_config(config_path)


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
    assert manifest["run_id"] == "RUN_TEST_001"
    assert manifest["timestamp_policy"]["source"] == "planned_target_synthetic_time"
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
