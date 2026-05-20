"""Create supplier invoice tool for SAP Fiori."""

from __future__ import annotations

import re
from time import monotonic

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.fiori_types import FioriDate, runtime_safe_fiori_date
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import RuntimeDelay, format_number, noop_delay, runtime_delay_callback


INVOICE_LINK_PATTERN = re.compile(r"(\d+)/(\d{4})")
BALANCE_NOT_ZERO_PATTERN = re.compile(r"Saldo\s+ist\s+ungleich\s+null", re.IGNORECASE)
NET_AMOUNT_TEXTBOX_NAME = re.compile(r"Nettobetrag|Netto\s*betrag", re.IGNORECASE)
SUPPLIER_INVOICE_READY_TIMEOUT_MS = 90_000
SUPPLIER_INVOICE_READY_POLL_MS = 1_000


class CreateSupplierInvoiceInput(BaseModel):
    """Input values for creating a supplier invoice against a purchase order."""

    model_config = ConfigDict(extra="forbid")

    invoice_date: FioriDate
    invoicing_party: str
    invoice_amount: float = Field(gt=0)
    purchase_order: str
    tax_code: str = "XI"

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_gross_amount(cls, value: object) -> object:
        if not isinstance(value, dict) or "gross_amount" not in value:
            return value

        migrated = dict(value)
        legacy_amount = migrated.pop("gross_amount")
        if "invoice_amount" in migrated and migrated["invoice_amount"] != legacy_amount:
            raise ValueError("invoice_amount and legacy gross_amount must match when both are provided")
        migrated.setdefault("invoice_amount", legacy_amount)
        return migrated


class SapSupplierInvoiceFlow:
    """Recorded SAP Fiori supplier invoice flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def create(self, params: CreateSupplierInvoiceInput) -> dict[str, str | float]:
        page = self._page
        invoice_date = runtime_safe_fiori_date(params.invoice_date)

        self._delay("app_open_search", 1.5)
        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Lieferantenrechnung anlegen")
        page.get_by_role("gridcell", name="Lieferantenrechnung anlegen", exact=True).locator("b").click()
        self._discard_existing_draft_if_present(page)

        self._delay("form_section_fill", 1.0)
        self._fill_textbox(page, "Rechnungsdatum", invoice_date)
        page.get_by_role("textbox", name="Rechnungsdatum").press("Tab")
        page.get_by_role("textbox", name="Buchungsdatum").press("Tab")

        net_amount = page.get_by_role("textbox", name=NET_AMOUNT_TEXTBOX_NAME)
        net_amount.click()
        net_amount.press("ControlOrMeta+a")
        net_amount.fill(format_number(params.invoice_amount))
        net_amount.press("Enter")

        self._fill_textbox(page, "Rechnungssteller", params.invoicing_party)
        page.get_by_role("textbox", name="Rechnungssteller").press("Enter")

        self._fill_textbox(page, "Bestellung/Lieferplan", params.purchase_order)
        page.get_by_role("textbox", name="Bestellung/Lieferplan").press("Enter")
        # This expected warning blocks the tax-code field until dismissed.
        page.get_by_role("button", name="Schließen").click()

        tax_code = page.get_by_role("textbox", name="Steuerkennzeichen")
        tax_code.click()
        tax_code.fill(params.tax_code)
        tax_code.press("Enter")

        page.get_by_role("button", name="Prüfen").click()
        self._raise_if_balance_not_zero(page, params)
        self._delay("review_save_post", 1.5)
        page.get_by_role("button", name="Buchen").click()

        invoice_link = page.locator("a", has_text=INVOICE_LINK_PATTERN).first
        invoice_link.wait_for(state="visible")
        invoice_text = invoice_link.inner_text()
        invoice, fiscal_year = _extract_invoice(invoice_text)
        self._click_no_if_present(page)
        return {
            "supplier_invoice": invoice,
            "fiscal_year": fiscal_year,
            "invoice_date": invoice_date,
            "requested_invoice_date": params.invoice_date,
            "invoicing_party": params.invoicing_party,
            "invoice_amount": params.invoice_amount,
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

    def _raise_if_balance_not_zero(self, page, params: CreateSupplierInvoiceInput) -> None:
        messages = page.handle_messages()
        for message in messages:
            message_text = str(getattr(message, "text", ""))
            if BALANCE_NOT_ZERO_PATTERN.search(message_text):
                raise ToolExecutionError(
                    "Supplier invoice balance is not zero after SAP check; "
                    f"purchase_order={params.purchase_order}; "
                    f"invoice_amount={params.invoice_amount}; "
                    f"tax_code={params.tax_code}; "
                    f"sap_message={message_text}"
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
