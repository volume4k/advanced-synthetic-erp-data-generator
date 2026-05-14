"""Send supplier payment tool for SAP Fiori."""

from __future__ import annotations

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.fiori_types import FioriCurrency, FioriDate
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec


PAYMENT_APP_NAME = "Ausgangszahlungen buchen"
SUPPLIER_ACCOUNT_INPUT_SELECTOR = (
    '[id="application-Supplier-postPayment-component---S1--'
    'fin.ap.payment.post.supplierAccountInput-input-inner"],'
    '[id*="supplierAccountInput"][id$="-inner"]'
)
OPEN_ITEM_ROW_XPATH = (
    "xpath=ancestor::*[@role='row'][1] | "
    "ancestor::*[contains(@id, 'openItems-rows-row') and not(contains(@id, '-col'))][1] | "
    "ancestor::tr[1]"
)
OPEN_ITEM_CLEAR_BUTTON_SELECTOR = '[id*="addIconToggleButton"]'
PAYMENT_DOCUMENT_SUCCESS_PATTERN = re.compile(r"Buchungsbeleg\s+(\d+)\s*\(")


class SendPaymentInput(BaseModel):
    """Input values for posting a supplier outgoing payment."""

    company_code: str
    posting_document_date: FioriDate
    supplier: str
    accounting_document: str
    general_ledger_account: str
    amount: float = Field(gt=0)
    currency: FioriCurrency = "USD"
    posting_date: FioriDate | None = None


class SapSendPaymentFlow:
    """Recorded SAP Fiori outgoing payment flow using a Fiori-aware page."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: SendPaymentInput) -> dict[str, str | float]:
        page = self._page

        self._open_payment_app(page)
        self._fill_textbox_if_visible(page, "Buchungskreis", params.company_code)
        self._fill_textbox(page, "Buchungsbelegdatum", params.posting_document_date, commit=True)
        if params.posting_date is not None:
            self._fill_textbox(page, "Buchungsdatum", params.posting_date, commit=True)
        self._fill_supplier(page, params.supplier)

        page.get_by_role("button", name="Posten anzeigen").click()
        self._clear_open_item(page, params.accounting_document)

        self._fill_textbox(page, "Sachkonto", params.general_ledger_account, commit=True)
        self._fill_textbox(page, "Betrag", _format_amount(params.amount), commit=True)
        self._fill_currency_if_visible(page, params.currency)

        page.get_by_role("button", name="Buchen", exact=True).click()
        payment_document = self._wait_for_payment_document(page)

        return {
            "payment_document": payment_document,
            "company_code": params.company_code,
            "posting_document_date": params.posting_document_date,
            "posting_date": params.posting_date or "",
            "supplier": params.supplier,
            "accounting_document": params.accounting_document,
            "general_ledger_account": params.general_ledger_account,
            "amount": params.amount,
            "currency": params.currency,
        }

    def _open_payment_app(self, page) -> None:
        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill(PAYMENT_APP_NAME)
        page.get_by_text(PAYMENT_APP_NAME).click(retry_on_next_wait=True)
        page.get_by_role("textbox", name="Buchungsbelegdatum").wait_for(state="visible")

    def _fill_supplier(self, page, supplier: str) -> None:
        supplier_input = page.locator(SUPPLIER_ACCOUNT_INPUT_SELECTOR).first
        try:
            supplier_input.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError:
            supplier_input = page.get_by_role(
                "textbox",
                name=re.compile(r"(Konto-ID|Konto.*ID|Kreditor)"),
            ).first
            supplier_input.wait_for(state="visible")

        supplier_input.click()
        supplier_input.press("ControlOrMeta+a")
        supplier_input.fill(supplier)
        supplier_input.press("Enter")

    def _clear_open_item(self, page, accounting_document: str) -> None:
        document_locator = self._open_item_document_locator(page, accounting_document)
        row = document_locator.locator(OPEN_ITEM_ROW_XPATH)

        try:
            row.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeoutError as exc:
            raise ToolExecutionError(
                f"Could not locate table row for open supplier item "
                f"'{accounting_document}'"
            ) from exc

        self._click_clear_button(row, accounting_document)

    def _open_item_document_locator(self, page, accounting_document: str):
        document_pattern = _accounting_document_pattern(accounting_document)
        candidates = [
            page.get_by_role("link", name=document_pattern).first,
            page.get_by_text(document_pattern).first,
        ]

        for candidate in candidates:
            try:
                candidate.wait_for(state="visible", timeout=10_000)
                return candidate
            except PlaywrightTimeoutError:
                continue

        raise ToolExecutionError(
            f"Could not find open supplier item with accounting document "
            f"'{accounting_document}'"
        )

    def _click_clear_button(self, row, accounting_document: str) -> None:
        button_pattern = re.compile("Ausgleichen")
        candidates = [
            row.get_by_role("button", name=button_pattern).first,
            row.locator(OPEN_ITEM_CLEAR_BUTTON_SELECTOR).first,
            row.get_by_text(button_pattern).first,
        ]

        for candidate in candidates:
            try:
                candidate.click(timeout=5000)
                return
            except PlaywrightTimeoutError:
                continue

        raise ToolExecutionError(
            f"Could not click clear button for open supplier item "
            f"'{accounting_document}'"
        )

    def _fill_textbox(self, page, name: str, value: str, *, commit: bool = False) -> None:
        textbox = page.get_by_role("textbox", name=name, exact=True)
        textbox.click()
        textbox.press("ControlOrMeta+a")
        textbox.fill(value)
        if commit:
            textbox.press("Enter")

    def _fill_textbox_if_visible(self, page, name: str, value: str) -> None:
        textbox = page.get_by_role("textbox", name=name, exact=True)
        try:
            textbox.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        self._fill_textbox(page, name, value, commit=True)

    def _fill_currency_if_visible(self, page, currency: str) -> None:
        currency_input = page.get_by_role("textbox", name="Währung Betrag")
        try:
            currency_input.wait_for(state="visible", timeout=3000)
        except PlaywrightTimeoutError:
            return
        currency_input.click()
        currency_input.press("ControlOrMeta+a")
        currency_input.fill(currency)
        currency_input.press("Enter")

    def _wait_for_payment_document(self, page) -> str:
        success_text = page.get_by_text(PAYMENT_DOCUMENT_SUCCESS_PATTERN).first
        success_text.wait_for(state="visible")
        return _extract_payment_document(success_text.inner_text())


def run_send_payment(
    context: ExecutionContext,
    params: SendPaymentInput,
) -> ToolResult:
    page = context.get_fiori_page()
    payment_data = SapSendPaymentFlow(page).create(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "posted",
            "current_url": page.url,
            "returned_objects": [
                returned_object(
                    "payment_document",
                    payment_document_number=payment_data["payment_document"],
                )
            ],
            **payment_data,
        },
    )


SEND_PAYMENT_TOOL = ToolSpec(
    name="fiori.send_payment",
    input_model=SendPaymentInput,
    run=run_send_payment,
)


def _extract_payment_document(message: str) -> str:
    match = PAYMENT_DOCUMENT_SUCCESS_PATTERN.search(message)
    if match is None:
        raise ValueError(
            f"Could not extract payment document number from success message: {message}"
        )
    return match.group(1)


def _accounting_document_pattern(accounting_document: str) -> re.Pattern[str]:
    escaped_document = re.escape(accounting_document)
    return re.compile(rf"(?:Buchungsbeleg\s+)?{escaped_document}\b")


def _format_amount(value: float) -> str:
    return f"{value:.2f}"
