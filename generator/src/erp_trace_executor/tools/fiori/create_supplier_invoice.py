"""Create supplier invoice tool for SAP Fiori."""

from __future__ import annotations

import re
from datetime import date
from time import monotonic

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import RuntimeDelay, format_number, noop_delay, runtime_delay_callback


INVOICE_LINK_PATTERN = re.compile(r"(\d+)/(\d{4})")
SUPPLIER_INVOICE_READY_TIMEOUT_MS = 90_000
SUPPLIER_INVOICE_READY_POLL_MS = 1_000


class CreateSupplierInvoiceInput(BaseModel):
    """Input values for creating a supplier invoice against a purchase order."""

    model_config = ConfigDict(extra="forbid")

    gross_amount: float = Field(gt=0)
    purchase_order: str
    tax_code: str = Field(min_length=1)


class SapSupplierInvoiceFlow:
    """Recorded SAP Fiori supplier invoice flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def create(self, params: CreateSupplierInvoiceInput) -> dict[str, str | float]:
        page = self._page
        invoice_date = _today_fiori_date()

        self._delay("app_open_search", 1.5)
        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Lieferantenrechnung anlegen")
        page.get_by_role("gridcell", name="Lieferantenrechnung anlegen", exact=True).locator("b").click()
        self._discard_existing_draft_if_present(page)

        self._delay("form_section_fill", 1.0)
        self._fill_textbox(page, "Rechnungsdatum", invoice_date)
        page.get_by_role("textbox", name="Rechnungsdatum").press("Enter")

        self._fill_textbox(page, "Bestellung/Lieferplan", params.purchase_order)
        self._delay("form_section_fill", 1.0)
        page.get_by_role("textbox", name="Bestellung/Lieferplan").press("Enter")
        self._delay("form_section_fill", 1.0)
        self._click_close_if_present(page)

        gross_amount = page.get_by_role("textbox", name="Bruttobetrag", exact=True)
        gross_amount.click()
        gross_amount.press("ControlOrMeta+a")
        gross_amount.fill(format_number(params.gross_amount))
        gross_amount.press("Enter")

        self._set_tax_code(page, params.tax_code)

        page.get_by_role("button", name="Prüfen").click()
        self._delay("review_save_post", 1.5)
        page.get_by_role("button", name="Buchen").click()

        invoice_link = page.locator("a", has_text=INVOICE_LINK_PATTERN).first
        invoice_link.wait_for(state="visible", timeout=SUPPLIER_INVOICE_READY_TIMEOUT_MS)
        invoice_text = invoice_link.inner_text()
        invoice, fiscal_year = _extract_invoice(invoice_text)
        self._click_no_if_present(page)
        return {
            "supplier_invoice": invoice,
            "fiscal_year": fiscal_year,
            "invoice_date": invoice_date,
            "gross_amount": params.gross_amount,
            "purchase_order": params.purchase_order,
            "tax_code": params.tax_code,
        }

    def _discard_existing_draft_if_present(self, page) -> None:
        if not self._wait_for_draft_or_invoice_form(page):
            return
        page.get_by_role("button", name="Nein").click()
        page.get_by_role("textbox", name="Rechnungsdatum").wait_for(
            state="visible",
            timeout=SUPPLIER_INVOICE_READY_TIMEOUT_MS,
        )

    def _wait_for_draft_or_invoice_form(self, page) -> bool:
        draft_message = page.get_by_text("Rechnungsentwurf vorhanden").first
        invoice_date = page.get_by_role("textbox", name="Rechnungsdatum")
        deadline = monotonic() + (SUPPLIER_INVOICE_READY_TIMEOUT_MS / 1000)

        while True:
            remaining_ms = int((deadline - monotonic()) * 1000)
            if remaining_ms <= 0:
                raise ToolExecutionError(
                    "Supplier invoice app did not show the draft dialog or invoice form before timeout"
                )
            poll_timeout = min(SUPPLIER_INVOICE_READY_POLL_MS, remaining_ms)

            try:
                draft_message.wait_for(state="visible", timeout=poll_timeout)
                return True
            except PlaywrightTimeoutError:
                pass

            try:
                invoice_date.wait_for(state="visible", timeout=poll_timeout)
                return False
            except PlaywrightTimeoutError:
                pass

    def _click_no_if_present(self, page) -> None:
        no_button = page.get_by_role("button", name="Nein")
        try:
            no_button.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        no_button.click()

    def _fill_textbox(self, page, name: str, value: str) -> None:
        textbox = page.get_by_role("textbox", name=name)
        textbox.click()
        textbox.press("ControlOrMeta+a")
        textbox.fill(value)

    def _click_close_if_present(self, page) -> None:
        close_button = page.get_by_role("button", name="Schließen")
        try:
            close_button.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        close_button.click()

    def _set_tax_code(self, page, value: str) -> None:
        tax_code = page.get_by_role("textbox", name="Steuerkennzeichen")
        tax_code.wait_for(state="visible")
        if str(tax_code.input_value()).strip() == value:
            return
        tax_code.click()
        tax_code.press("ControlOrMeta+a")
        tax_code.fill(value)
        tax_code.press("Enter")
        current_value = str(tax_code.input_value()).strip()
        if current_value != value:
            raise ToolExecutionError(
                f"Failed to set supplier invoice tax code to '{value}'; current value is '{current_value}'"
            )


def run_create_supplier_invoice(
    context: ExecutionContext,
    params: CreateSupplierInvoiceInput,
) -> ToolResult:
    page = context.get_fiori_page()
    invoice_data = SapSupplierInvoiceFlow(page, delay=runtime_delay_callback(context)).create(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            "returned_objects": [
                returned_object(
                    "supplier_invoice",
                    invoice_number=invoice_data["supplier_invoice"],
                    fiscal_year=invoice_data["fiscal_year"],
                )
            ],
            **invoice_data,
        },
    )


CREATE_SUPPLIER_INVOICE_TOOL = ToolSpec(
    name="fiori.create_supplier_invoice",
    input_model=CreateSupplierInvoiceInput,
    run=run_create_supplier_invoice,
)


def _extract_invoice(message: str) -> tuple[str, str]:
    match = INVOICE_LINK_PATTERN.search(message)
    if match is None:
        raise ValueError(f"Could not extract supplier invoice number from success link: {message}")
    return match.group(1), match.group(2)


def _today_fiori_date(today: date | None = None) -> str:
    return (today or date.today()).strftime("%m/%d/%Y")
