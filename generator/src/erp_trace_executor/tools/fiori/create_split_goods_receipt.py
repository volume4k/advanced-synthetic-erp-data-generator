"""Create split goods receipt tool for SAP Fiori."""

from __future__ import annotations

import re
from typing import Literal

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.runtime_delay import RuntimeDelay, noop_delay, runtime_delay_callback
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number

MATERIAL_DOCUMENT_LINK_PATTERN = re.compile(r"Materialbeleg\s*(\d+)(?:/\d+)?")
NO_SELECTABLE_POSITION_PATTERN = re.compile(
    r"Beleg\s+\d+\s+enthält keine wählbare Position"
)
QUANTITY_SELECTOR = 'input[placeholder="0.000"]'
STOCK_TYPE_CELL_SELECTOR = '[id*="idStockType_inputCell"][id$="-label"]'
STORAGE_LOCATION_CELL_SELECTOR = '[id*="idStorageLocation_inputCell"][id$="-inner"]'
STORAGE_LOCATION_OPTION_SELECTOR = 'span[id*="idStorageLocation"]'
StorageLocation = Literal["Finished Goods", "Trading Goods", "Miscellaneous", "Returns"]


class CreateSplitGoodsReceiptInput(BaseModel):
    """Input values for posting one purchase-order receipt into two stock states."""

    model_config = ConfigDict(extra="forbid")

    purchase_order: str = Field(min_length=1)
    storage_location: StorageLocation
    unrestricted_quantity: float = Field(gt=0)
    quality_inspection_quantity: float = Field(gt=0)


class SapSplitGoodsReceiptFlow:
    """Recorded SAP Fiori split goods receipt flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def create(self, params: CreateSplitGoodsReceiptInput) -> dict[str, str | float]:
        page = self._page

        self._delay("app_open_search", 1.5)
        app_url = f"{page.url.split('#', 1)[0]}#PurchaseOrder-createGR&/"
        page.goto(app_url)
        try:
            page.get_by_role("textbox", name="Einkaufsbeleg").wait_for(
                state="visible",
                timeout=30_000,
            )
        except PlaywrightTimeoutError:
            page.get_by_role("button", name="Suche öffnen").click()
            page.get_by_role("searchbox", name="Suchen").fill(
                "Wareneingang zu Einkaufsbeleg buchen"
            )
            tile = page.get_by_role("gridcell", name="Wareneingang zu Einkaufsbeleg").locator("b")
            try:
                tile.click(timeout=60_000, retry_on_next_wait=True)
            except PlaywrightTimeoutError:
                page.goto(app_url)
            page.get_by_role("textbox", name="Einkaufsbeleg").wait_for(
                state="visible",
                timeout=90_000,
            )

        purchase_order = page.get_by_role("textbox", name="Einkaufsbeleg")
        purchase_order.wait_for(state="visible")
        self._delay("form_section_fill", 1.0)
        purchase_order.click()
        purchase_order.fill(params.purchase_order)
        if purchase_order.input_value() != params.purchase_order:
            page.wait_for_timeout(500)
            purchase_order.fill(params.purchase_order)
        current_purchase_order = purchase_order.input_value()
        if current_purchase_order != params.purchase_order:
            raise ToolExecutionError(
                f"Failed to fill purchase order field with '{params.purchase_order}'; current value is "
                f"'{current_purchase_order}'"
            )
        purchase_order.press("Enter")
        self._raise_if_no_selectable_position(page, params.purchase_order)

        self._delay("receipt_position_review", 1.3)
        page.get_by_role("button", name="Splitposition").click()

        page.locator(STORAGE_LOCATION_CELL_SELECTOR).first.click()
        page.locator(STORAGE_LOCATION_OPTION_SELECTOR, has_text=params.storage_location).first.click()
        unrestricted_quantity = page.raw_page.locator(QUANTITY_SELECTOR).nth(0)
        _fill_all(unrestricted_quantity, format_number(params.unrestricted_quantity))
        unrestricted_quantity.press("Enter")
        page.wait_until_ready()

        page.locator(STOCK_TYPE_CELL_SELECTOR).last.click()
        page.get_by_role("option", name="Qualitätsprüfung").click()
        page.locator(STORAGE_LOCATION_CELL_SELECTOR).last.click()
        page.locator(STORAGE_LOCATION_OPTION_SELECTOR, has_text=params.storage_location).first.click()
        quality_quantity = page.raw_page.locator(QUANTITY_SELECTOR).nth(1)
        _fill_all(quality_quantity, format_number(params.quality_inspection_quantity))
        quality_quantity.press("Enter")
        page.wait_until_ready()

        self._delay("review_save_post", 2.0)
        page.get_by_role("button", name="Buchen", exact=True).click()

        success_dialog = page.locator('[role="dialog"]', has_text="Materialbeleg").first
        success_dialog.wait_for(state="visible")
        material_document = _extract_material_document(success_dialog.inner_text())

        page.get_by_role("button", name="OK").click()
        return {
            "material_document": material_document,
            "purchase_order": params.purchase_order,
            "storage_location": params.storage_location,
            "unrestricted_quantity": params.unrestricted_quantity,
            "quality_inspection_quantity": params.quality_inspection_quantity,
        }

    def _raise_if_no_selectable_position(self, page, purchase_order: str) -> None:
        message = page.get_by_text(NO_SELECTABLE_POSITION_PATTERN).first
        try:
            message.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return

        raise ToolExecutionError(
            f"Purchase order '{purchase_order}' contains no selectable goods receipt position: {message.inner_text()}"
        )


def run_create_split_goods_receipt(
    context: ExecutionContext,
    params: CreateSplitGoodsReceiptInput,
) -> ToolResult:
    page = context.get_fiori_page()
    goods_receipt_data = SapSplitGoodsReceiptFlow(page, delay=runtime_delay_callback(context)).create(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            "returned_objects": [
                returned_object("material_document", material_document_number=goods_receipt_data["material_document"])
            ],
            **goods_receipt_data,
        },
    )


CREATE_SPLIT_GOODS_RECEIPT_TOOL = ToolSpec(
    name="fiori.create_split_goods_receipt",
    input_model=CreateSplitGoodsReceiptInput,
    run=run_create_split_goods_receipt,
)


def _fill_all(locator, value: str) -> None:
    locator.click()
    locator.press("ControlOrMeta+a")
    locator.fill(value)


def _extract_material_document(message: str) -> str:
    match = MATERIAL_DOCUMENT_LINK_PATTERN.search(message)
    if match is None:
        raise ValueError(
            f"Could not extract material document number from success link: {message}"
        )
    return match.group(1)
