"""Manage quality-inspection stock tool for SAP Fiori."""

from __future__ import annotations

import re
from typing import Literal

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.runtime_delay import RuntimeDelay, noop_delay, runtime_delay_callback
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number

MANAGE_STOCK_APP_HASH = "#Material-manageStock?sap_mmim_apptype=manage&/"
MATERIAL_DOCUMENT_LINK_PATTERN = re.compile(r"Materialbeleg\s*(\d+)(?:/\d+)?")
COST_CENTER_INPUT_SELECTOR = "#idCostCenterInput-inner"
DOCUMENT_ITEM_TEXT_INPUT_SELECTOR = "#idInputDocumentItemText-inner"
POST_BUTTON_SELECTOR = "#idPostButton"
MOVEMENT_LABELS = {
    "scrap": "Verschrottung",
    "release_to_unrestricted": "Bestandsaufnahme",
}
MOVEMENT_OBJECT_TYPES = {
    "scrap": "scrap_material_document",
    "release_to_unrestricted": "stock_release_material_document",
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
            self._select_cost_center(params.cost_center)

        self._fill_document_item_text(params.document_item_text)

        self._delay("review_save_post", 2.0)
        page.locator(POST_BUTTON_SELECTOR).click()

        material_document = _extract_material_document(_read_material_document_success_text(page))

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

    def _select_cost_center(self, cost_center: str) -> None:
        page = self._page
        page.locator("#idCostCenterInput-vhi").click()
        dialog = page.get_by_label("Kostenstelle auswählen")
        dialog.wait_for(state="visible", timeout=30_000)

        search = dialog.locator(
            'input[role="searchbox"], input[type="search"], input[placeholder*="Suchen"], input[aria-label*="Suchen"]'
        ).first
        try:
            search.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            pass
        else:
            search.click()
            search.press("ControlOrMeta+a")
            search.fill(cost_center)
            search.press("Enter")

        dialog.get_by_text(cost_center).first.click()
        page.locator(COST_CENTER_INPUT_SELECTOR).wait_for(state="visible", timeout=30_000)

    def _fill_document_item_text(self, document_item_text: str) -> None:
        page = self._page
        item_text_by_id = page.locator(DOCUMENT_ITEM_TEXT_INPUT_SELECTOR)
        try:
            item_text_by_id.wait_for(state="visible", timeout=30_000)
            item_text_by_id.click()
            item_text_by_id.fill(document_item_text)
        except PlaywrightTimeoutError:
            pass
        else:
            return

        for item_text in (
            page.get_by_role("textbox", name="Belegpositionstext"),
            page.get_by_label("Belegpositionstext"),
            page.locator(
                'input[aria-label="Belegpositionstext"], textarea[aria-label="Belegpositionstext"], '
                'input[title="Belegpositionstext"], textarea[title="Belegpositionstext"]'
            ),
        ):
            try:
                item_text.click(timeout=3000)
                item_text.fill(document_item_text)
            except PlaywrightTimeoutError:
                continue
            return

        header_text = page.get_by_role("textbox", name="Kopftext")
        header_text.click(timeout=3000)
        header_text.press("Tab")
        page.raw_page.keyboard.insert_text(document_item_text)
        page.wait_until_ready()


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
                returned_object(
                    MOVEMENT_OBJECT_TYPES[params.movement],
                    material_document_number=movement_data["material_document"],
                )
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


def _read_material_document_success_text(page) -> str:
    success_dialog = page.locator('[role="dialog"]', has_text="Materialbeleg").first
    success_dialog.wait_for(state="visible", timeout=60_000)
    return success_dialog.inner_text()
