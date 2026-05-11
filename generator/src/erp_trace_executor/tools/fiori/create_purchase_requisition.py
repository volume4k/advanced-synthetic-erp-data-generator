"""Create purchase requisition tool for Fiori."""

from __future__ import annotations

import re
from time import monotonic

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.fiori_types import FioriDate
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.helpers import format_number
from erp_trace_executor.tools.fiori.pages import FixtureFioriPage

PURCHASE_REQUISITION_READY_TIMEOUT_MS = 90_000
PURCHASE_REQUISITION_READY_POLL_MS = 1_000
PURCHASE_REQUISITION_DRAFT_GRACE_MS = 5_000


class CreatePurchaseRequisitionInput(BaseModel):
    material: str
    quantity: int = Field(gt=0)
    valuation_price: float = Field(gt=0)
    currency: str
    price_unit: int = Field(gt=0)
    delivery_date: FioriDate
    plant: str
    purchasing_group: str
    purchasing_organization: str
    company_code: str


class SapPurchaseRequisitionFlow:
    """Recorded SAP Fiori purchase requisition flow using a Fiori-aware page."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: CreatePurchaseRequisitionInput) -> dict[str, str | int]:
        page = self._page

        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Bestellanforderung anle")
        page.get_by_text("Bestellanforderung anlegen").click()
        self._discard_existing_draft_if_present(page)

        material_field = self._open_new_position(page)
        material_field.click()
        material_field.fill(params.material)
        material_field.press("Enter")

        page.get_by_role("textbox", name="Bewertungspreis", exact=True).click()
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).press("ControlOrMeta+a")
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).fill(format_number(params.valuation_price))
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).press("Tab")
        page.get_by_role("textbox", name="Währung Bewertungspreis").fill(params.currency)
        page.get_by_role("textbox", name="Währung Bewertungspreis").press("Tab")
        page.get_by_role("textbox", name="Preiseinheit").fill(str(params.price_unit))
        page.get_by_role("textbox", name="Preiseinheit").press("Tab")
        page.get_by_role("textbox", name="Anforderungsmenge").fill(str(params.quantity))
        page.get_by_role("textbox", name="Anforderungsmenge").press("Tab")

        page.get_by_label("Auswahl öffnen").click()
        page.get_by_role("textbox", name="Lieferdatum").dblclick()
        page.get_by_role("textbox", name="Lieferdatum").press("ControlOrMeta+a")
        page.get_by_role("textbox", name="Lieferdatum").fill(params.delivery_date)
        page.get_by_role("textbox", name="Lieferdatum").press("Enter")
        page.locator("#application-PurchaseRequisition-create-component---Freetext--simpleForm--Form--Grid").click()
        page.get_by_role("button", name="Zu Einkaufswagen hinzufügen").click()

        page.get_by_title("Navigation", exact=True).click()
        page.get_by_role("textbox", name="Einkäufergruppe").click()
        page.get_by_role("textbox", name="Einkäufergruppe").fill(params.purchasing_group)
        page.get_by_role("textbox", name="Einkäufergruppe").press("Tab")
        page.get_by_role("textbox", name="EinkOrganisation").fill(params.purchasing_organization)
        page.get_by_role("textbox", name="EinkOrganisation").press("Tab")
        page.get_by_role("textbox", name="Buchungskreis").fill(params.company_code)
        page.get_by_role("textbox", name="Buchungskreis").press("Tab")
        page.get_by_role("textbox", name="Werk").fill(params.plant)
        page.get_by_role("textbox", name="Werk").press("Enter")
        page.get_by_role("button", name="Sichern", exact=True).click()

        page.get_by_role("textbox", name="Bewertungspreis", exact=True).click()
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).dblclick()
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).fill(format_number(params.valuation_price))
        page.locator("#application-PurchaseRequisition-create-component---ItemDetails--smartForm1--Form--Grid").click()
        page.get_by_role("button", name="Sichern", exact=True).click()
        page.get_by_role("button", name="Zurück").click()
        page.get_by_role("button", name="1").click()
        page.get_by_role("button", name="Bestellen").click()

        requisition_link = page.locator("#idPRNoLinkId")
        requisition_link.wait_for(state="visible")
        return {
            "purchase_requisition": requisition_link.inner_text(),
            "material": params.material,
            "quantity": params.quantity,
        }

    def _textbox(self, name: str):
        return self._page.get_by_role("textbox", name=re.compile(re.escape(name)))

    def _open_new_position(self, page):
        position_button = page.get_by_role("button", name="Position anlegen", exact=True)
        material_field = self._textbox("Material")
        max_attempts = 3

        for _attempt in range(max_attempts):
            position_button.click(retry_on_next_wait=True)
            try:
                material_field.wait_for(state="visible", recover_fiori_messages=False)
                return material_field
            except PlaywrightTimeoutError as exc:
                if self._discard_existing_draft_if_present(page):
                    continue
                raise PlaywrightTimeoutError(
                    "position_button click did not make material_field visible, "
                    "and _discard_existing_draft_if_present found no draft dialog"
                ) from exc

        raise PlaywrightTimeoutError(
            f"position_button failed to open material_field after {max_attempts} attempts"
        )

    def _discard_existing_draft_if_present(self, page) -> bool:
        if not self._wait_for_draft_dialog(page):
            return False
        page.get_by_role("button", name="Verwerfen").click()
        page.get_by_role("button", name="Position anlegen", exact=True).wait_for(
            state="visible",
            timeout=PURCHASE_REQUISITION_READY_TIMEOUT_MS,
        )
        return True

    def _wait_for_draft_dialog(self, page, *, timeout_ms: int = PURCHASE_REQUISITION_DRAFT_GRACE_MS) -> bool:
        draft_message = page.get_by_text("Entwurf der Bestellanforderung").first
        deadline = monotonic() + (timeout_ms / 1000)

        while True:
            remaining_ms = int((deadline - monotonic()) * 1000)
            if remaining_ms <= 0:
                return False
            poll_timeout = min(PURCHASE_REQUISITION_READY_POLL_MS, remaining_ms)

            try:
                draft_message.wait_for(state="visible", timeout=poll_timeout)
                return True
            except PlaywrightTimeoutError:
                pass


def run_create_purchase_requisition(
    context: ExecutionContext,
    params: CreatePurchaseRequisitionInput,
) -> ToolResult:
    session = context.get_browser_session()
    page = session.page

    if page.get_by_test_id("session-shell").is_visible():
        requisition_data = FixtureFioriPage(page).create_purchase_requisition(**params.model_dump())
    else:
        requisition_data = SapPurchaseRequisitionFlow(context.get_fiori_page()).create(params)

    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            "returned_objects": [
                returned_object("purchase_requisition", pr_number=requisition_data["purchase_requisition"])
            ],
            **requisition_data,
        },
    )


CREATE_PURCHASE_REQUISITION_TOOL = ToolSpec(
    name="fiori.create_purchase_requisition",
    input_model=CreatePurchaseRequisitionInput,
    run=run_create_purchase_requisition,
)
