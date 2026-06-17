"""Create purchase order with changed delivery address tool for SAP Fiori."""

from __future__ import annotations

import re
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.runtime_delay import RuntimeDelay, noop_delay, runtime_delay_callback
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number


IFRAME_SELECTOR = 'iframe[name="application-PurchaseOrder-create-iframe"]'
PURCHASE_ORDER_CREATE_HASH = "#PurchaseOrder-create?sap-ui-tech-hint=GUI"
STATUS_BAR_MESSAGE_SELECTOR = '[id="wnd[0]/sbar_msg-txt"]'
SUCCESS_MESSAGE_PATTERN = re.compile(r"Normalbestellung unter der Nummer\s+(\d+)\s+angelegt")


class PurchaseOrderDeliveryAddress(BaseModel):
    """Delivery address values for a purchase order line."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    street_and_house_number: str = Field(min_length=1)
    house_number: str = Field(min_length=1)
    postal_code: str = Field(min_length=1)
    city: str = Field(min_length=1)
    country: str = Field(min_length=1)
    region: str = Field(min_length=1)


class CreatePurchaseOrderWithDeliveryAddressInput(BaseModel):
    """Input values for creating a purchase order with a manipulated delivery address."""

    model_config = ConfigDict(extra="forbid")

    purchase_requisition: str = Field(min_length=1)
    storage_location: str = Field(min_length=1)
    supplier: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    net_price: float = Field(gt=0)
    delivery_address: PurchaseOrderDeliveryAddress
    tax_code: str = "XI"


class SapPurchaseOrderWithDeliveryAddressFlow:
    """Recorded SAP Fiori purchase order flow extended with delivery-address overwrite."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def create(self, params: CreatePurchaseOrderWithDeliveryAddressInput) -> dict[str, str | int | float | dict[str, str]]:
        page = self._page

        self._delay("app_open_search", 1.5)
        page.goto(f"{page.url.split('#', 1)[0]}{PURCHASE_ORDER_CREATE_HASH}")

        frame = page.locator(IFRAME_SELECTOR).content_frame
        frame.get_by_role("textbox", name="Lieferant").wait_for(state="visible", timeout=90_000)
        self._close_start_dialog_if_visible(frame)

        self._delay("supplier_context_review", 1.0)
        supplier = frame.get_by_role("textbox", name="Lieferant")
        supplier.click()
        supplier.fill(params.supplier)
        supplier.press("Enter")

        self._delay("form_section_fill", 1.0)
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").wait_for(state="visible")
        frame.get_by_role("button", name="Positionen aufklappen Strg+F3").click()

        self._focus_purchase_requisition_by_tab(frame)
        purchase_requisition = frame.get_by_role("textbox", name="Banf").first
        purchase_requisition.wait_for(state="visible")
        self._delay("line_item_reference_review", 1.4)
        self._type_grid_textbox_value(purchase_requisition, params.purchase_requisition)
        self._delay("line_item_import_review", 1.5)

        quantity_input = frame.get_by_role("textbox", name="Bestellmenge").first
        quantity_input.wait_for(state="visible")
        self._type_grid_textbox_value(quantity_input, str(params.quantity))
        net_price_input = frame.get_by_role("textbox", name="Nettopreis").first
        net_price_input.wait_for(state="visible")
        self._type_grid_textbox_value(net_price_input, format_number(params.net_price))
        self._delay("pricing_tax_review", 1.6)
        frame.get_by_role("tablist").get_by_text("Rechnung", exact=True).click()
        tax_code = frame.get_by_role("textbox", name="Steuerkennz.")
        tax_code.click()
        tax_code.fill(params.tax_code)
        tax_code.press("Enter")

        self._delay("delivery_address_review", 1.8)
        frame.get_by_role("tablist").get_by_text("Anlieferadresse", exact=True).click()
        self._fill_delivery_address(frame, params.delivery_address)

        self._delay("review_save_post", 2.0)
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
            "delivery_address": params.delivery_address.model_dump(),
        }

    def _focus_purchase_requisition_by_tab(self, frame) -> None:
        """Move through SAP GUI item table by keyboard until the Banf cell exists."""

        frame.get_by_role("textbox", name="Bestellmenge").first.press("Tab")
        frame.get_by_role("textbox", name="Charge").first.press("Tab")
        frame.get_by_role("textbox", name="Bestandssegment").first.press("Tab")
        frame.get_by_role("textbox", name="BedarfsNr.").first.press("Tab")
        frame.get_by_role("textbox", name="Anforderer").first.press("Tab")
        frame.get_by_role("textbox", name="Art der Lohnbearbeitung").first.press("Tab")
        frame.get_by_role("textbox", name="Infosatz").first.press("Tab")
        frame.get_by_role("checkbox").first.press("Tab")
        frame.get_by_role("checkbox").nth(1).press("Tab")
        frame.get_by_role("textbox", name="Banf").first.wait_for(state="visible")

    def _type_grid_textbox_value(self, cell, value: str) -> None:
        """Type into the current SAP GUI grid textbox and commit it."""

        for character in value:
            cell.press(character)
            time.sleep(0.1)
        time.sleep(0.25)
        cell.press("Enter")

    def _fill_delivery_address(self, frame, address: PurchaseOrderDeliveryAddress) -> None:
        self._fill_textbox(frame.get_by_role("textbox", name="Name", exact=True), address.name)
        frame.get_by_role("textbox", name="Name", exact=True).press("Tab")
        frame.get_by_role("textbox", name="Name 2").press("Tab")
        self._fill_textbox(
            frame.get_by_role("textbox", name="Straße/Hausnummer"),
            address.street_and_house_number,
        )
        frame.get_by_role("textbox", name="Straße/Hausnummer").press("Tab")
        self._fill_textbox(
            frame.get_by_role("textbox", name="Hausnummer", exact=True),
            address.house_number,
        )
        frame.get_by_role("textbox", name="Hausnummer", exact=True).press("Tab")
        frame.get_by_role("textbox", name="Ortsteil").press("Tab")
        self._fill_textbox(
            frame.get_by_role("textbox", name="Postleitzahl/Ort"),
            address.postal_code,
        )
        frame.get_by_role("textbox", name="Postleitzahl/Ort").press("Tab")
        self._fill_textbox(frame.get_by_role("textbox", name="Ort", exact=True), address.city)
        frame.get_by_role("textbox", name="Ort", exact=True).press("Tab")
        self._fill_textbox(frame.get_by_role("textbox", name="Land/Region"), address.country)
        frame.get_by_role("textbox", name="Land/Region").press("Enter")
        self._fill_textbox(frame.get_by_role("textbox", name="Region", exact=True), address.region)
        frame.get_by_role("textbox", name="Region", exact=True).press("Enter")

    def _fill_textbox(self, textbox, value: str) -> None:
        textbox.click()
        textbox.press("ControlOrMeta+a")
        textbox.fill(value)

    def _close_start_dialog_if_visible(self, frame) -> None:
        close_button = frame.get_by_role("button", name="Schließen")
        try:
            close_button.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        close_button.click()


def run_create_purchase_order_with_delivery_address(
    context: ExecutionContext,
    params: CreatePurchaseOrderWithDeliveryAddressInput,
) -> ToolResult:
    page = context.get_fiori_page()
    order_data = SapPurchaseOrderWithDeliveryAddressFlow(
        page,
        delay=runtime_delay_callback(context),
    ).create(params)

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


CREATE_PURCHASE_ORDER_WITH_DELIVERY_ADDRESS_TOOL = ToolSpec(
    name="fiori.create_purchase_order_with_delivery_address",
    input_model=CreatePurchaseOrderWithDeliveryAddressInput,
    run=run_create_purchase_order_with_delivery_address,
)


def _extract_purchase_order(message: str) -> str:
    match = SUCCESS_MESSAGE_PATTERN.search(message)
    if match is None:
        raise ValueError(f"Could not extract purchase order number from success message: {message}")
    return match.group(1)
