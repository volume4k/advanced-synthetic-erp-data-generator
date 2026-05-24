"""Change vendor bank details tool for SAP Fiori."""

from __future__ import annotations

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult
from erp_trace_executor.runtime_delay import RuntimeDelay, noop_delay, runtime_delay_callback
from erp_trace_executor.tooling import ToolSpec


BUSINESS_PARTNER_APP_HASH = "#BusinessPartner-manage"
BUSINESS_PARTNER_READY_TIMEOUT_MS = 90_000
BANK_DETAILS_TIMEOUT_MS = 30_000
BANK_KEY_PATTERNS = (
    re.compile(r"^Bankschlüssel$", re.IGNORECASE),
    re.compile(r"^Bank Key$", re.IGNORECASE),
    re.compile(r"^(SWIFT|BIC|SWIFT/BIC)$", re.IGNORECASE),
    re.compile(r"(Bankschlüssel|Bank Key|SWIFT|BIC)", re.IGNORECASE),
)


class VendorBankAccountCredentials(BaseModel):
    """Bank account values for an existing vendor."""

    model_config = ConfigDict(extra="forbid")

    bank_key: str = Field(min_length=1)
    account_number: str = Field(min_length=1)
    account_owner: str = Field(min_length=1)


class ChangeVendorBankDetailsInput(BaseModel):
    """Input values for changing existing vendor bank-account details."""

    model_config = ConfigDict(extra="forbid")

    vendor_id: str = Field(min_length=1)
    bank_account_credentials: VendorBankAccountCredentials


class SapVendorBankDetailsFlow:
    """Recorded SAP Fiori business partner flow using a Fiori-aware page."""

    def __init__(self, page, delay: RuntimeDelay = noop_delay) -> None:
        self._page = page
        self._delay = delay

    def change(self, params: ChangeVendorBankDetailsInput) -> dict[str, str]:
        page = self._page
        credentials = params.bank_account_credentials

        self._delay("app_open_direct", 1.2)
        page.goto(f"{page.url.split('#', 1)[0]}{BUSINESS_PARTNER_APP_HASH}")
        business_partner = page.get_by_role("textbox", name="Geschäftspartner:")
        business_partner.wait_for(state="visible", timeout=BUSINESS_PARTNER_READY_TIMEOUT_MS)

        self._delay("vendor_lookup_review", 1.3)
        self._fill_textbox(business_partner, params.vendor_id)
        business_partner.press("Enter")
        page.get_by_role("button", name="Start").click()
        vendor_row = page.get_by_role("gridcell", name="Navigation").first
        vendor_row.wait_for(state="visible", timeout=BANK_DETAILS_TIMEOUT_MS)

        self._delay("vendor_select_review", 1.2)
        vendor_row.click()
        edit_button = page.get_by_role("button", name="Bearbeiten")
        edit_button.wait_for(state="visible", timeout=BUSINESS_PARTNER_READY_TIMEOUT_MS)

        self._delay("edit_mode_review", 1.0)
        edit_button.click()
        bank_accounts = page.get_by_role("option", name="Bankkonten")
        bank_accounts.wait_for(state="visible", timeout=BANK_DETAILS_TIMEOUT_MS)
        bank_accounts.click()

        bank_details = page.get_by_label("Objektdetails").locator("[title='Navigation']").first
        bank_details.wait_for(state="visible", timeout=BANK_DETAILS_TIMEOUT_MS)
        self._delay("bank_details_review", 1.4)
        bank_details.click()
        account_number = page.get_by_label("Kontonummer")
        account_number.wait_for(state="visible", timeout=BANK_DETAILS_TIMEOUT_MS)

        self._delay("bank_credentials_review", 1.8)
        self._fill_bank_key(credentials.bank_key)
        self._fill_textbox(account_number, credentials.account_number)
        self._fill_textbox(page.get_by_label("Kontoinhaber"), credentials.account_owner)

        page.get_by_role("button", name="Übernehmen").click()
        save_button = page.get_by_role("button", name="Sichern")
        save_button.wait_for(state="visible", timeout=BANK_DETAILS_TIMEOUT_MS)

        self._delay("review_save_post", 2.5)
        save_button.click()
        page.wait_until_ready()

        return {
            "vendor_id": params.vendor_id,
            "bank_key": credentials.bank_key,
            "account_number": credentials.account_number,
            "account_owner": credentials.account_owner,
        }

    def _fill_textbox(self, textbox, value: str) -> None:
        textbox.click()
        textbox.press("ControlOrMeta+a")
        textbox.fill(value)

    def _fill_bank_key(self, value: str) -> None:
        for pattern in BANK_KEY_PATTERNS:
            candidates = (
                self._page.get_by_label(pattern).first,
                self._page.get_by_role("textbox", name=pattern).first,
            )
            for candidate in candidates:
                try:
                    candidate.wait_for(state="visible", timeout=3000)
                except PlaywrightTimeoutError:
                    continue
                self._fill_textbox(candidate, value)
                return

        raise ToolExecutionError("Could not locate visible bank key field for vendor bank details")


def run_change_vendor_bank_details(
    context: ExecutionContext,
    params: ChangeVendorBankDetailsInput,
) -> ToolResult:
    page = context.get_fiori_page()
    bank_data = SapVendorBankDetailsFlow(page, delay=runtime_delay_callback(context)).change(params)

    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "updated",
            "current_url": page.url,
            **bank_data,
        },
    )


CHANGE_VENDOR_BANK_DETAILS_TOOL = ToolSpec(
    name="fiori.change_vendor_bank_details",
    input_model=ChangeVendorBankDetailsInput,
    run=run_change_vendor_bank_details,
)
