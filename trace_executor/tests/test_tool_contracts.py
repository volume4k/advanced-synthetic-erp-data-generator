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
from erp_trace_executor.tools.fiori.create_split_goods_receipt import (
    _extract_material_document_year as extract_split_goods_receipt_material_document_year,
)
from erp_trace_executor.tools.fiori.manage_quality_inspection_stock import (
    DOCUMENT_ITEM_TEXT_INPUT_SELECTOR,
    SapQualityInspectionStockFlow,
    _extract_material_document as extract_quality_inspection_material_document,
)
from erp_trace_executor.tools.fiori.manage_quality_inspection_stock import (
    _extract_material_document_year as extract_quality_inspection_material_document_year,
)
from erp_trace_executor.tools.fiori.manage_quality_inspection_stock import (
    _read_material_document_success_text,
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
    "sap-larceny5-manual.execution-trace.yaml",
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
    assert extract_quality_inspection_material_document_year("Materialbeleg 4900038011/2026") == "2026"
    assert (
        extract_split_goods_receipt_material_document("Materialbeleg5000000127/")
        == "5000000127"
    )
    assert extract_split_goods_receipt_material_document_year("Materialbeleg5000000127/") is None


def test_quality_stock_success_reader_uses_dialog_text_not_role_regex():
    class FakeLocator:
        def __init__(self, text: str) -> None:
            self.text = text
            self.wait_kwargs = None

        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            self.wait_kwargs = kwargs

        def inner_text(self) -> str:
            return self.text

    class FakePage:
        def __init__(self) -> None:
            self.dialog = FakeLocator("Erfolg Materialbeleg 4900038013/2026 angelegt OK")
            self.locator_calls = []

        def locator(self, *args, **kwargs):
            self.locator_calls.append((args, kwargs))
            return self.dialog

        def get_by_role(self, *args, **kwargs):  # pragma: no cover - proves the old path is gone
            raise AssertionError("success reader must not use role regex locator")

    page = FakePage()

    assert _read_material_document_success_text(page) == "Erfolg Materialbeleg 4900038013/2026 angelegt OK"
    assert page.locator_calls == [(('[role="dialog"]',), {"has_text": "Materialbeleg"})]
    assert page.dialog.wait_kwargs == {"state": "visible", "timeout": 60_000}


def test_quality_stock_item_text_prefers_observed_sap_input_id():
    class FakeLocator:
        def __init__(self) -> None:
            self.events = []

        def wait_for(self, **kwargs):
            self.events.append(("wait_for", kwargs))

        def click(self, **kwargs):
            self.events.append(("click", kwargs))

        def fill(self, value: str):
            self.events.append(("fill", value))

    class FakePage:
        def __init__(self) -> None:
            self.item_text = FakeLocator()
            self.locator_selectors = []

        def locator(self, selector: str):
            self.locator_selectors.append(selector)
            return self.item_text

        def get_by_role(self, *args, **kwargs):  # pragma: no cover - proves id path wins
            raise AssertionError("role fallback should not be used when SAP id is visible")

    page = FakePage()

    SapQualityInspectionStockFlow(page)._fill_document_item_text("Qualitätsprüfung bestanden")

    assert page.locator_selectors == [DOCUMENT_ITEM_TEXT_INPUT_SELECTOR]
    assert page.item_text.events == [
        ("wait_for", {"state": "visible", "timeout": 30_000}),
        ("click", {}),
        ("fill", "Qualitätsprüfung bestanden"),
    ]
