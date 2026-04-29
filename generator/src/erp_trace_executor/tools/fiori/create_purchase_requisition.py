"""Create purchase requisition tool for Fiori."""

from __future__ import annotations

from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.pages import FixtureFioriPage


class CreatePurchaseRequisitionInput(BaseModel):
    material: str
    quantity: int = Field(gt=0)
    valuation_price: float = Field(gt=0)
    currency: str
    price_unit: int = Field(gt=0)
    delivery_date: str
    plant: str
    purchasing_group: str
    purchasing_organization: str
    company_code: str


class SapPurchaseRequisitionFlow:
    """Recorded SAP Fiori purchase requisition flow."""

    def __init__(self, page) -> None:
        self._page = page

    def create(self, params: CreatePurchaseRequisitionInput) -> dict[str, str | int]:
        page = self._page

        page.get_by_role("button", name="Suche öffnen").click()
        page.get_by_role("searchbox", name="Suchen").fill("Bestellanforderung anle")
        page.get_by_text("Bestellanforderung anlegen").click()
        page.get_by_role("button", name="Position anlegen", exact=True).click()

        page.get_by_role("textbox", name="Material", exact=True).click()
        page.get_by_role("textbox", name="Material", exact=True).fill(params.material)
        page.get_by_role("textbox", name="Material", exact=True).press("Enter")

        page.get_by_role("textbox", name="Bewertungspreis", exact=True).click()
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).press("ControlOrMeta+a")
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).fill(_format_number(params.valuation_price))
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
        page.get_by_role("textbox", name="Bewertungspreis", exact=True).fill(_format_number(params.valuation_price))
        page.locator("#application-PurchaseRequisition-create-component---ItemDetails--smartForm1--Form--Grid").click()
        page.get_by_role("button", name="Sichern", exact=True).click()
        page.get_by_role("button", name="Zurück").click()
        page.get_by_role("button", name="1").click()
        page.get_by_role("button", name="Bestellen").click()
        page.get_by_role("button", name="Bestellen").click()

        requisition_link = page.locator("#idPRNoLinkId")
        requisition_link.wait_for(state="visible")
        return {
            "purchase_requisition": requisition_link.inner_text(),
            "material": params.material,
            "quantity": params.quantity,
        }


def run_create_purchase_requisition(
    context: ExecutionContext,
    params: CreatePurchaseRequisitionInput,
) -> ToolResult:
    session = context.get_browser_session()
    page = session.page

    if page.get_by_test_id("session-shell").is_visible():
        requisition_data = FixtureFioriPage(page).create_purchase_requisition(**params.model_dump())
    else:
        requisition_data = SapPurchaseRequisitionFlow(page).create(params)

    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "current_url": page.url,
            **requisition_data,
        },
    )


CREATE_PURCHASE_REQUISITION_TOOL = ToolSpec(
    name="fiori.create_purchase_requisition",
    input_model=CreatePurchaseRequisitionInput,
    run=run_create_purchase_requisition,
)


def _format_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return str(value)
