"""Login tool for the local Fiori fixture app."""

from __future__ import annotations

from pydantic import BaseModel, HttpUrl

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.pages import FixtureFioriPage


class LoginInput(BaseModel):
    base_url: HttpUrl
    username: str
    password: str


def run_login(context: ExecutionContext, params: LoginInput) -> ToolResult:
    session = context.get_browser_session()
    page = FixtureFioriPage(session.page)
    page.goto(str(params.base_url))
    page.login(params.username, params.password)
    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "logged_in",
            "username": params.username,
        },
    )


LOGIN_TOOL = ToolSpec(
    name="fiori.login",
    input_model=LoginInput,
    run=run_login,
)
