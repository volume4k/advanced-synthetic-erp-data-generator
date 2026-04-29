from __future__ import annotations

from types import SimpleNamespace

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
    def __init__(self) -> None:
        self.filled_value: str | None = None
        self.clicked = False

    def fill(self, value: str) -> None:
        self.filled_value = value

    def click(self) -> None:
        self.clicked = True

    def wait_for(self, *, state: str) -> None:
        self.state = state


class FakePage:
    def __init__(self) -> None:
        self.url = "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"
        self.visited_url: str | None = None
        self.waited_state: str | None = None
        self.locators: dict[str, FakeLocator] = {}

    def goto(self, url: str) -> None:
        self.visited_url = url

    def locator(self, selector: str) -> FakeLocator:
        if selector not in self.locators:
            self.locators[selector] = FakeLocator()
        return self.locators[selector]

    def wait_for_load_state(self, state: str) -> None:
        self.waited_state = state


def test_login_waits_for_load_when_no_success_selector_is_configured():
    page = FakePage()
    context = SimpleNamespace(
        record=SimpleNamespace(task_id="task-1", session_id="session-1", tool="fiori.login"),
        get_browser_session=lambda: SimpleNamespace(page=page),
    )
    params = LoginInput.model_validate({"username": "buyer-a", "password": "secret"})

    result = run_login(context, params)

    assert page.waited_state == "load"
    assert result.data["status"] == "logged_in"
