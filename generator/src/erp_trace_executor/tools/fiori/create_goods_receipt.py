"""Create goods receipt tool for SAP Fiori."""

from __future__ import annotations

import re
from typing import Literal

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec

MATERIAL_DOCUMENT_LINK_PATTERN = re.compile(r"Materialbeleg\s+(\d+)/?")
NO_SELECTABLE_POSITION_PATTERN = re.compile(
    r"Beleg\s+\d+\s+enthält keine wählbare Position"
)
STORAGE_LOCATION_CELL_SELECTOR = '[id*="idStorageLocation_inputCell"][id$="-inner"]'
STORAGE_LOCATION_OPTION_SELECTOR = 'span[id*="idStorageLocation"]'
StorageLocation = Literal["Finished Goods", "Trading Goods", "Miscellaneous", "Returns"]


class CreateGoodsReceiptInput(BaseModel):
    """Input values for posting a goods receipt against a purchase order."""

    purchase_order: str
    document_date: str
    posting_date: str
    storage_location: StorageLocation


class SapGoodsReceiptFlow:
    """Recorded SAP Fiori goods receipt flow using a Fiori-aware page."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: CreateGoodsReceiptInput) -> dict[str, str]:
        page = self._page

        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill(
            "Wareneingang zu Einkaufsbeleg buchen"
        )
        page.get_by_role("gridcell", name="Wareneingang zu Einkaufsbeleg").locator(
            "b"
        ).click(retry_on_next_wait=True)

        purchase_order = page.get_by_role("textbox", name="Einkaufsbeleg")
        purchase_order.wait_for(state="visible")
        purchase_order.click()
        purchase_order.fill(params.purchase_order)
        purchase_order.press("Enter")
        self._raise_if_no_selectable_position(page, params.purchase_order)

        self._fill_textbox(page, "Belegdatum", params.document_date)
        self._fill_textbox(page, "Buchungsdatum", params.posting_date)

        page.locator(STORAGE_LOCATION_CELL_SELECTOR).first.click()
        page.locator(
            STORAGE_LOCATION_OPTION_SELECTOR, has_text=params.storage_location
        ).first.click()
        page.get_by_role("button", name="Buchen", exact=True).click()

        success_dialog = page.locator('[role="dialog"]', has_text="Materialbeleg").first
        success_dialog.wait_for(state="visible")
        material_document = _extract_material_document(success_dialog.inner_text())

        page.get_by_role("button", name="OK").click()
        return {
            "material_document": material_document,
            "purchase_order": params.purchase_order,
            "document_date": params.document_date,
            "posting_date": params.posting_date,
            "storage_location": params.storage_location,
        }

    def _fill_textbox(self, page, name: str, value: str) -> None:
        textbox = page.get_by_role("textbox", name=name)
        textbox.click()
        textbox.press("ControlOrMeta+a")
        textbox.fill(value)

    def _raise_if_no_selectable_position(self, page, purchase_order: str) -> None:
        message = page.get_by_text(NO_SELECTABLE_POSITION_PATTERN).first
        try:
            message.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return

        raise ToolExecutionError(
            f"Purchase order '{purchase_order}' contains no selectable goods receipt position: {message.inner_text()}"
        )


def run_create_goods_receipt(
    context: ExecutionContext,
    params: CreateGoodsReceiptInput,
) -> ToolResult:
    page = context.get_fiori_page()
    goods_receipt_data = SapGoodsReceiptFlow(page).create(params)

    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
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


CREATE_GOODS_RECEIPT_TOOL = ToolSpec(
    name="fiori.create_goods_receipt",
    input_model=CreateGoodsReceiptInput,
    run=run_create_goods_receipt,
)


def _extract_material_document(message: str) -> str:
    match = MATERIAL_DOCUMENT_LINK_PATTERN.search(message)
    if match is None:
        raise ValueError(
            f"Could not extract material document number from success link: {message}"
        )
    return match.group(1)
