from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from pydantic import BaseModel
from pydantic import ValidationError

from erp_trace_executor.canonical import load_canonical_trace
from erp_trace_executor.registry import build_default_registry
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.create_split_goods_receipt import (
    _extract_material_document as extract_split_goods_receipt_material_document,
)
from erp_trace_executor.tools.fiori.manage_quality_inspection_stock import (
    _extract_material_document as extract_quality_inspection_material_document,
)

EXAMPLES_DIR = Path(__file__).parents[1] / "examples"
INFRASTRUCTURE_TOOLS = {"fiori.login"}
EXPECTED_EXAMPLE_TRACES = {
    "sap-change-vendor-bank-details.execution-trace.yaml",
    "sap-create-goods-receipt.execution-trace.yaml",
    "sap-create-purchase-order.execution-trace.yaml",
    "sap-create-purchase-requisition.execution-trace.yaml",
    "sap-create-supplier-invoice.execution-trace.yaml",
    "sap-init-login.execution-trace.yaml",
    "sap-larceny3-manual.execution-trace.yaml",
    "sap-procure-to-pay-runtime-handover.execution-trace.yaml",
    "sap-send-payment.execution-trace.yaml",
}


def test_default_registry_tool_specs_satisfy_core_contract():
    registry = build_default_registry()
    names = registry.names()

    assert names
    assert len(names) == len(set(names))

    for name in names:
        spec = registry.get(name)
        signature = inspect.signature(spec.run)

        assert isinstance(spec, ToolSpec)
        assert spec.name == name
        assert issubclass(spec.input_model, BaseModel)
        assert callable(spec.run)
        assert len(signature.parameters) == 2


def test_registered_business_tools_have_valid_example_trace_inputs():
    registry = build_default_registry()
    seen_tools: set[str] = set()
    trace_paths = sorted(EXAMPLES_DIR.glob("*.execution-trace.yaml"))

    assert {path.name for path in trace_paths} == EXPECTED_EXAMPLE_TRACES

    for trace_path in trace_paths:
        trace = load_canonical_trace(trace_path)
        for session in trace.actor_sessions:
            assert session.password_env_var, f"{trace_path} must reference a password env var"

        for node in trace.dependency_graph.planned_steps:
            assert "password" not in node.inputs, f"{trace_path} must not embed passwords in node inputs"
            try:
                spec = registry.get(node.tool_name)
            except Exception as exc:
                raise AssertionError(
                    f"{trace_path} node '{node.planned_step_id}' references unregistered tool '{node.tool_name}'"
                ) from exc
            spec.input_model.model_validate(node.inputs)
            seen_tools.add(node.tool_name)

    business_tools = set(registry.names()) - INFRASTRUCTURE_TOOLS
    missing_examples = business_tools - seen_tools

    assert missing_examples == set()


def test_goods_receipt_tool_rejects_runtime_date_inputs():
    spec = build_default_registry().get("fiori.create_goods_receipt")

    spec.input_model.model_validate(
        {
            "purchase_order": "4500001234",
            "storage_location": "Trading Goods",
        }
    )
    with pytest.raises(ValidationError):
        spec.input_model.model_validate(
            {
                "purchase_order": "4500001234",
                "document_date": "05/14/2026",
                "posting_date": "05/14/2026",
                "storage_location": "Trading Goods",
            }
        )


def test_larceny3_tool_inputs_validate_and_reject_extra_fields():
    registry = build_default_registry()

    split_goods_receipt = registry.get("fiori.create_split_goods_receipt")
    split_goods_receipt.input_model.model_validate(
        {
            "purchase_order": "4500001234",
            "storage_location": "Trading Goods",
            "unrestricted_quantity": 40,
            "quality_inspection_quantity": 10,
        }
    )
    with pytest.raises(ValidationError):
        split_goods_receipt.input_model.model_validate(
            {
                "purchase_order": "4500001234",
                "storage_location": "Trading Goods",
                "unrestricted_quantity": 40,
                "quality_inspection_quantity": 10,
                "posting_date": "05/14/2026",
            }
        )

    quality_stock = registry.get("fiori.manage_quality_inspection_stock")
    quality_stock.input_model.model_validate(
        {
            "material": "CHSP1800",
            "stock_location_label": "DC Miami",
            "movement": "scrap",
            "quantity": 5,
            "cost_center": "NAPC1000",
            "document_item_text": "Muss leider verschrottet werden.",
        }
    )
    quality_stock.input_model.model_validate(
        {
            "material": "CHSP1800",
            "stock_location_label": "DC Miami",
            "movement": "release_to_unrestricted",
            "quantity": 5,
            "document_item_text": "Qualitätsprüfung bestanden",
        }
    )
    with pytest.raises(ValidationError):
        quality_stock.input_model.model_validate(
            {
                "material": "CHSP1800",
                "stock_location_label": "DC Miami",
                "movement": "scrap",
                "quantity": 5,
                "document_item_text": "Muss leider verschrottet werden.",
            }
        )
    with pytest.raises(ValidationError):
        quality_stock.input_model.model_validate(
            {
                "material": "CHSP1800",
                "stock_location_label": "DC Miami",
                "movement": "release_to_unrestricted",
                "quantity": 5,
                "document_item_text": "Qualitätsprüfung bestanden",
                "unexpected": "value",
            }
        )


def test_larceny3_material_document_extractors_accept_recorded_message_shapes():
    assert (
        extract_quality_inspection_material_document("Materialbeleg 4900038011/2026")
        == "4900038011"
    )
    assert (
        extract_split_goods_receipt_material_document("Materialbeleg5000000127/")
        == "5000000127"
    )
