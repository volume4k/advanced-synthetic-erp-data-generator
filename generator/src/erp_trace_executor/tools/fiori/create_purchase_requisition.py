"""Create purchase requisition tool for Fiori."""

from __future__ import annotations

from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.pages import FixtureFioriPage, PurchaseRequisitionPage


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


def run_create_purchase_requisition(
    context: ExecutionContext,
    params: CreatePurchaseRequisitionInput,
) -> ToolResult:
    session = context.get_browser_session()
    page = session.page

    if page.get_by_test_id("session-shell").is_visible():
        requisition_data = FixtureFioriPage(page).create_purchase_requisition(**params.model_dump())
    else:
        requisition_page = PurchaseRequisitionPage(page)
        requisition_page.goto()
        requisition_data = requisition_page.create(**params.model_dump())

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
