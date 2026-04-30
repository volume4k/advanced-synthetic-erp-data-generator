"""Create purchase order tool for SAP Fiori."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec


IFRAME_SELECTOR = 'iframe[name="application-PurchaseOrder-create-iframe"]'
SUCCESS_MESSAGE_PATTERN = re.compile(r"Normalbestellung unter der Nummer\s+(\d+)\s+angelegt")


class CreatePurchaseOrderInput(BaseModel):
    """Input values for creating a purchase order from a purchase requisition."""

    purchase_requisition: str
    storage_location: str
    supplier: str
    quantity: int = Field(gt=0)


class SapPurchaseOrderFlow:
    """Recorded SAP Fiori purchase order flow using a Fiori-aware page."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: CreatePurchaseOrderInput) -> dict[str, str | int]:
        page = self._page

        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Bestellung")
        page.get_by_text("Bestellung anlegen in Apps").click()
        page.get_by_title("Bestellung anlegen").click(retry_on_next_wait=True)

        frame = page.locator(IFRAME_SELECTOR).content_frame
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").wait_for(state="visible")
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").click()
        self._scroll_to_purchase_requisition(frame)

        purchase_requisition = frame.get_by_role("textbox", name="Banf").first
        purchase_requisition.click()
        purchase_requisition.fill(params.purchase_requisition)
        purchase_requisition.press("Enter")

        frame.locator("img").click()
        frame.locator("img").dblclick()
        self._scroll_to_storage_location(frame)

        frame.get_by_role("textbox", name="Lagerort").first.click()
        frame.get_by_role("grid").locator('input[name="InputField"]').fill(params.storage_location)
        frame.get_by_role("grid").locator('input[name="InputField"]').press("Enter")
        frame.get_by_role("button", name="Schließen").click()

        supplier = frame.get_by_role("textbox", name="Lieferant")
        supplier.click()
        supplier.fill(params.supplier)
        supplier.press("Enter")

        frame.locator("img").click()
        frame.locator('#u51050 input[name="InputField"]').fill(str(params.quantity))
        frame.locator('#u51050 input[name="InputField"]').press("Enter")
        frame.get_by_role("button", name=re.compile(r"Sichern\s+Hervorgehoben")).click()

        success_message = frame.get_by_text(SUCCESS_MESSAGE_PATTERN)
        success_message.wait_for(state="visible")
        message = success_message.inner_text()
        purchase_order = _extract_purchase_order(message)
        return {
            "purchase_order": purchase_order,
            "purchase_requisition": params.purchase_requisition,
            "storage_location": params.storage_location,
            "supplier": params.supplier,
            "quantity": params.quantity,
        }

    def _scroll_to_purchase_requisition(self, frame) -> None:
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator('[id="M0:46:1:3:2:1:1_hscroll-bar"]').click()
        frame.locator('[id="M0:46:1:3:2:1:1-mrss-cont-none"]').click()
        frame.locator('[id="M0:46:1:3:2:1:1_hscroll-bar"]').click()
        frame.locator('[id="M0:46:1:3:2:1:1_hscroll-bar"]').click()
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator('[id="M0:46:1:3:2:1:1_hscroll-bar"]').click()
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()

    def _scroll_to_storage_location(self, frame) -> None:
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator(".urSCBBtn.urBorderBox.lsScrollbar--inlineBlock").click()
        frame.locator('[id="M0:46:1:3:2:1:1_hscroll-bar"]').dblclick()


def run_create_purchase_order(
    context: ExecutionContext,
    params: CreatePurchaseOrderInput,
) -> ToolResult:
    page = context.get_fiori_page()
    order_data = SapPurchaseOrderFlow(page).create(params)

    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            **order_data,
        },
    )


CREATE_PURCHASE_ORDER_TOOL = ToolSpec(
    name="fiori.create_purchase_order",
    input_model=CreatePurchaseOrderInput,
    run=run_create_purchase_order,
)


def _extract_purchase_order(message: str) -> str:
    match = SUCCESS_MESSAGE_PATTERN.search(message)
    if match is None:
        raise ValueError(f"Could not extract purchase order number from success message: {message}")
    return match.group(1)
