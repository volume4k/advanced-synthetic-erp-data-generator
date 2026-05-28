"""Manage quality-inspection stock tool for SAP Fiori."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.runtime_delay import RuntimeDelay, noop_delay, runtime_delay_callback
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number

MANAGE_STOCK_APP_HASH = "#Material-manageStock?sap_mmim_apptype=manage&/"
MATERIAL_DOCUMENT_LINK_PATTERN = re.compile(r"Materialbeleg\s*(\d+)(?:/\d+)?")
MOVEMENT_LABELS = {
    "scrap": "Verschrottung",
    "release_to_unrestricted": "Bestandsaufnahme",
}
QualityInspectionMovement = Literal["scrap", "release_to_unrestricted"]


class ManageQualityInspectionStockInput(BaseModel):
    """Input values for posting a movement from quality-inspection stock."""

    model_config = ConfigDict(extra="forbid")

    material: str = Field(min_length=1)
    stock_location_label: str = Field(min_length=1)
    movement: QualityInspectionMovement
    quantity: float = Field(gt=0)
    document_item_text: str = Field(min_length=1)
    cost_center: str | None = None

    @model_validator(mode="after")
    def require_cost_center_for_scrap(self) -> "ManageQualityInspectionStockInput":
        if self.movement == "scrap" and not self.cost_center:
            raise ValueError("cost_center is required when movement is scrap")
        return self


class SapQualityInspectionStockFlow:
    """Recorded SAP Fiori manage-stock flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def post(self, params: ManageQualityInspectionStockInput) -> dict[str, str | float]:
        page = self._page

        self._delay("app_open_search", 1.5)
        page.goto(f"{page.url.split('#', 1)[0]}{MANAGE_STOCK_APP_HASH}")
        page.get_by_label("Auswahloptionen").wait_for(state="visible", timeout=90_000)

        self._delay("stock_filter_review", 1.2)
        page.get_by_label("Auswahloptionen").click()
        page.get_by_text(params.stock_location_label).click()
        material = page.get_by_role("textbox", name="Material")
        material.click()
        material.fill(params.material)
        material.press("Enter")

        self._delay("quality_stock_review", 1.3)
        page.get_by_role("button", name="Bestand in Qualitätsprüfung").click()
        page.locator("#idManageStockType-arrow").click()
        page.get_by_role("option", name=MOVEMENT_LABELS[params.movement]).click()

        quantity = page.get_by_role("spinbutton", name="Menge")
        quantity.click()
        quantity.press("ControlOrMeta+a")
        quantity.fill(format_number(params.quantity))

        if params.movement == "scrap" and params.cost_center is not None:
            cost_center = page.get_by_role("textbox", name="Kostenstelle")
            cost_center.click()
            cost_center.fill(params.cost_center)
            cost_center.press("Enter")

        item_text = page.get_by_role("textbox", name="Belegpositionstext")
        item_text.click()
        item_text.fill(params.document_item_text)

        self._delay("review_save_post", 2.0)
        page.get_by_role("button", name="Buchen").click()

        success_link = page.get_by_role("link", name=MATERIAL_DOCUMENT_LINK_PATTERN).first
        success_link.wait_for(state="visible", timeout=60_000)
        material_document = _extract_material_document(success_link.inner_text())

        page.get_by_role("button", name="OK").click()
        return {
            "material_document": material_document,
            "material": params.material,
            "stock_location_label": params.stock_location_label,
            "movement": params.movement,
            "quantity": params.quantity,
            "document_item_text": params.document_item_text,
            "cost_center": params.cost_center,
        }


def run_manage_quality_inspection_stock(
    context: ExecutionContext,
    params: ManageQualityInspectionStockInput,
) -> ToolResult:
    page = context.get_fiori_page()
    movement_data = SapQualityInspectionStockFlow(page, delay=runtime_delay_callback(context)).post(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "posted",
            "current_url": page.url,
            "returned_objects": [
                returned_object("material_document", material_document_number=movement_data["material_document"])
            ],
            **movement_data,
        },
    )


MANAGE_QUALITY_INSPECTION_STOCK_TOOL = ToolSpec(
    name="fiori.manage_quality_inspection_stock",
    input_model=ManageQualityInspectionStockInput,
    run=run_manage_quality_inspection_stock,
)


def _extract_material_document(message: str) -> str:
    match = MATERIAL_DOCUMENT_LINK_PATTERN.search(message)
    if match is None:
        raise ValueError(
            f"Could not extract material document number from success link: {message}"
        )
    return match.group(1)
