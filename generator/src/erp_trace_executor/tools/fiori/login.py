"""Configurable login tool for Fiori-style apps."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, HttpUrl

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec


SAP_FIORI_LOGIN_URL = "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"
DEFAULT_USERNAME_SELECTOR = "#USERNAME_FIELD-inner"
DEFAULT_PASSWORD_SELECTOR = "#PASSWORD_FIELD-inner"
DEFAULT_SUBMIT_SELECTOR = "#LOGIN_LINK"


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl | None = None
    base_url: HttpUrl | None = None
    username: str
    password: str
    username_selector: str = DEFAULT_USERNAME_SELECTOR
    password_selector: str = DEFAULT_PASSWORD_SELECTOR
    submit_selector: str = DEFAULT_SUBMIT_SELECTOR
    success_selector: str | None = None

    def resolved_login_url(self) -> str:
        return str(self.url or self.base_url or SAP_FIORI_LOGIN_URL)


def run_login(context: ExecutionContext, params: LoginInput) -> ToolResult:
    session = context.get_browser_session()
    page = session.page
    login_url = params.resolved_login_url()

    page.goto(login_url)
    page.locator(params.username_selector).fill(params.username)
    page.locator(params.password_selector).fill(params.password)
    page.locator(params.submit_selector).click()

    if params.success_selector is not None:
        page.locator(params.success_selector).wait_for(state="visible")
    else:
        page.wait_for_load_state("load")
        if _login_form_still_visible(page, params):
            raise ToolExecutionError(
                "Login did not reach a post-authenticated state; configure success_selector for this login form"
            )

    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "logged_in",
            "username": params.username,
            "url": login_url,
            "current_url": page.url,
        },
    )


LOGIN_TOOL = ToolSpec(
    name="fiori.login",
    input_model=LoginInput,
    run=run_login,
)


def _login_form_still_visible(page, params: LoginInput) -> bool:
    return any(
        page.locator(selector).is_visible()
        for selector in (
            params.username_selector,
            params.password_selector,
            params.submit_selector,
        )
    )
