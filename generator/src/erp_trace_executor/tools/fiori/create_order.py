"""Follow-up business action tool for the local Fiori fixture app."""

from __future__ import annotations

from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.pages import FixtureFioriPage


class CreateOrderInput(BaseModel):
    item_name: str
    quantity: int = Field(ge=1)


def run_create_order(context: ExecutionContext, params: CreateOrderInput) -> ToolResult:
    session = context.get_browser_session()
    page = FixtureFioriPage(session.page)
    page.ensure_logged_in(context.record.user_id)
    order_data = page.create_order(params.item_name, params.quantity)
    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data=order_data,
    )


CREATE_ORDER_TOOL = ToolSpec(
    name="fiori.create_order",
    input_model=CreateOrderInput,
    run=run_create_order,
)
