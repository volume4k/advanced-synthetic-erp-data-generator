from __future__ import annotations

from types import SimpleNamespace

import pytest

from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.tools.fiori.login import SAP_FIORI_LOGIN_URL, LoginInput
from erp_trace_executor.tools.fiori.login import run_login


def test_login_input_defaults_to_sap_tour_url():
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    assert str(params.resolved_login_url()) == SAP_FIORI_LOGIN_URL


def test_login_input_keeps_base_url_compatibility():
    params = LoginInput.model_validate(
        {
            "base_url": "http://127.0.0.1:8000/index.html",
            "username": "buyer-a",
            "password": "secret",
        }
    )

    assert str(params.resolved_login_url()) == "http://127.0.0.1:8000/index.html"


class FakeLocator:
    def __init__(self, *, selector: str = "", events: list[tuple] | None = None, visible: bool = False) -> None:
        self.selector = selector
        self.events = events
        self.filled_value: str | None = None
        self.clicked = False
        self.visible = visible

    def fill(self, value: str) -> None:
        if self.events is not None:
            self.events.append(("fill", self.selector, value))
        self.filled_value = value

    def click(self) -> None:
        self.clicked = True

    def wait_for(self, *, state: str) -> None:
        self.state = state

    def is_visible(self) -> bool:
        return self.visible


class FakePage:
    def __init__(self) -> None:
        self.url = "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"
        self.visited_url: str | None = None
        self.waited_state: str | None = None
        self.locators: dict[str, FakeLocator] = {}
        self.events: list[tuple] = []

    def goto(self, url: str) -> None:
        self.visited_url = url

    def locator(self, selector: str) -> FakeLocator:
        self.events.append(("locator", selector))
        if selector not in self.locators:
            self.locators[selector] = FakeLocator(selector=selector, events=self.events)
        return self.locators[selector]

    def wait_for_load_state(self, state: str) -> None:
        self.waited_state = state

    def wait_for_timeout(self, timeout: int) -> None:
        self.events.append(("wait_for_timeout", timeout))


def test_login_waits_for_load_when_no_success_selector_is_configured():
    page = FakePage()
    context = SimpleNamespace(
        record=SimpleNamespace(planned_step_id="planned-step-1", actor_session_id="session-1", tool="fiori.login"),
        get_browser_session=lambda: SimpleNamespace(page=page),
    )
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    result = run_login(context, params)

    assert page.waited_state == "load"
    assert result.data["status"] == "logged_in"


def test_login_pauses_between_username_and_password_entry():
    page = FakePage()
    context = SimpleNamespace(
        record=SimpleNamespace(planned_step_id="planned-step-1", actor_session_id="session-1", tool="fiori.login"),
        get_browser_session=lambda: SimpleNamespace(page=page),
    )
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    run_login(context, params)

    assert page.events[:5] == [
        ("locator", "#USERNAME_FIELD-inner"),
        ("fill", "#USERNAME_FIELD-inner", "buyer-a"),
        ("wait_for_timeout", 50),
        ("locator", "#PASSWORD_FIELD-inner"),
        ("fill", "#PASSWORD_FIELD-inner", "secret"),
    ]


def test_login_rejects_visible_login_form_after_load_without_success_selector():
    page = FakePage()
    page.locators["#LOGIN_LINK"] = FakeLocator(visible=True)
    context = SimpleNamespace(
        record=SimpleNamespace(planned_step_id="planned-step-1", actor_session_id="session-1", tool="fiori.login"),
        get_browser_session=lambda: SimpleNamespace(page=page),
    )
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    with pytest.raises(ToolExecutionError, match="success_selector"):
        run_login(context, params)
