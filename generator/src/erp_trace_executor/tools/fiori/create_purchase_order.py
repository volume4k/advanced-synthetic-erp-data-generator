"""Create purchase order tool for SAP Fiori."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number


IFRAME_SELECTOR = 'iframe[name="application-PurchaseOrder-create-iframe"]'
PURCHASE_ORDER_CREATE_HASH = "#PurchaseOrder-create?sap-ui-tech-hint=GUI"
STATUS_BAR_MESSAGE_SELECTOR = '[id="wnd[0]/sbar_msg-txt"]'
SUCCESS_MESSAGE_PATTERN = re.compile(r"Normalbestellung unter der Nummer\s+(\d+)\s+angelegt")


class CreatePurchaseOrderInput(BaseModel):
    """Input values for creating a purchase order from a purchase requisition."""

    purchase_requisition: str
    storage_location: str
    supplier: str
    quantity: int = Field(gt=0)
    net_price: float = Field(gt=0)
    tax_code: str = "XI"


class SapPurchaseOrderFlow:
    """Recorded SAP Fiori purchase order flow using a Fiori-aware page."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: CreatePurchaseOrderInput) -> dict[str, str | int | float]:
        page = self._page

        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Bestellung")
        page.get_by_text("Bestellung anlegen in Apps").click()
        page.locator(f'a[href*="{PURCHASE_ORDER_CREATE_HASH}"]').click(retry_on_next_wait=True)

        frame = page.locator(IFRAME_SELECTOR).content_frame
        self._close_start_dialog_if_visible(frame)
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").wait_for(state="visible")
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").click()

        self._focus_purchase_requisition_by_tab(frame)
        self._fill_grid_textbox(frame, "Banf", params.purchase_requisition)

        supplier = frame.get_by_role("textbox", name="Lieferant")
        supplier.click()
        supplier.fill(params.supplier)
        supplier.press("Enter")

        frame.locator("img").click()
        quantity_input = frame.get_by_role("textbox", name="Bestellmenge").first
        quantity_input.wait_for(state="visible")
        self._replace_grid_textbox_value(quantity_input, str(params.quantity))
        net_price_input = frame.get_by_role("textbox", name="Nettopreis").first
        net_price_input.wait_for(state="visible")
        self._type_selected_grid_textbox_value(net_price_input, format_number(params.net_price))
        frame.get_by_role("tablist").get_by_text("Rechnung").click()
        tax_code = frame.get_by_role("textbox", name="Steuerkennz.")
        tax_code.click()
        tax_code.fill(params.tax_code)
        tax_code.press("Enter")
        frame.get_by_role("button", name=re.compile(r"Sichern\s+Hervorgehoben")).click()

        success_message = frame.locator(STATUS_BAR_MESSAGE_SELECTOR)
        success_message.wait_for(state="visible")
        message = success_message.inner_text()
        purchase_order = _extract_purchase_order(message)
        return {
            "purchase_order": purchase_order,
            "purchase_requisition": params.purchase_requisition,
            "storage_location": params.storage_location,
            "supplier": params.supplier,
            "quantity": params.quantity,
            "net_price": params.net_price,
            "tax_code": params.tax_code,
        }

    def _focus_purchase_requisition_by_tab(self, frame) -> None:
        """Move through SAP GUI item table by keyboard until the Banf cell exists."""

        frame.get_by_role("textbox", name="Bestellmenge").first.click(retry_on_next_wait=True)
        frame.get_by_role("grid").locator('input[name="InputField"]').press("Tab")
        frame.get_by_role("textbox", name="Charge").first.press("Tab")
        frame.get_by_role("textbox", name="Bestandssegment").first.press("Tab")
        frame.get_by_role("textbox", name="BedarfsNr.").first.press("Tab")
        frame.get_by_role("textbox", name="Anforderer").first.press("Tab")
        frame.get_by_role("textbox", name="Art der Lohnbearbeitung").first.press("Tab")
        frame.get_by_role("textbox", name="Infosatz").first.press("Tab")
        frame.get_by_role("checkbox").first.press("Tab")
        frame.get_by_role("checkbox").nth(1).press("Tab")
        frame.get_by_role("textbox", name="Banf").first.wait_for(state="visible")

    def _fill_grid_textbox(self, frame, label: str, value: str) -> None:
        """Fill one SAP GUI grid textbox after it has been reached in the table."""

        cell = frame.get_by_role("textbox", name=label).first
        cell.wait_for(state="visible")
        self._replace_grid_textbox_value(cell, value)

    def _replace_grid_textbox_value(self, cell, value: str) -> None:
        """Replace SAP GUI grid text where the textbox may be a focusable span."""

        cell.click()
        cell.press("ControlOrMeta+a")
        for character in value:
            cell.press(character)
        cell.press("Enter")

    def _type_selected_grid_textbox_value(self, cell, value: str) -> None:
        """Type into a SAP GUI grid textbox whose current value is already selected."""

        for character in value:
            cell.press(character)
        cell.press("Enter")

    def _close_start_dialog_if_visible(self, frame) -> None:
        close_button = frame.get_by_role("button", name="Schließen")
        try:
            close_button.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        close_button.click()


def run_create_purchase_order(
    context: ExecutionContext,
    params: CreatePurchaseOrderInput,
) -> ToolResult:
    page = context.get_fiori_page()
    order_data = SapPurchaseOrderFlow(page).create(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            "returned_objects": [
                returned_object("purchase_order", po_number=order_data["purchase_order"])
            ],
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
