from __future__ import annotations

from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError
from pydantic import BaseModel

from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.errors import SessionUserMismatchError, StateResolutionError, ToolInputValidationError, UnknownToolError
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.models import ToolResult, TraceDefinition, TraceInitRecord, TraceInitUser, TraceRecord, returned_object
from erp_trace_executor.registry import ToolRegistry
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.login import LOGIN_TOOL


class ProducePurchaseRequisitionInput(BaseModel):
    pr_number: str


class ConsumePurchaseRequisitionInput(BaseModel):
    purchase_requisition: str


class NoInput(BaseModel):
    pass


def _purchase_requisition_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "material": "PUMP1902",
        "quantity": 20,
        "valuation_price": 30,
        "currency": "USD",
        "price_unit": 1,
        "delivery_date": "05/20/2026",
        "plant": "MI00",
        "purchasing_group": "N00",
        "purchasing_organization": "US00",
        "company_code": "US00",
    }
    payload.update(overrides)
    return payload


def _state_test_registry(captured_inputs: list[ConsumePurchaseRequisitionInput] | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    def run_produce(context, params: ProducePurchaseRequisitionInput) -> ToolResult:
        return ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
            tool=context.tool,
            data={
                "returned_objects": [
                    returned_object("purchase_requisition", pr_number=params.pr_number)
                ],
            },
        )

    def run_consume(context, params: ConsumePurchaseRequisitionInput) -> ToolResult:
        if captured_inputs is not None:
            captured_inputs.append(params)
        return ToolResult(
            task_id=context.task_id,
            session_id=context.session_id,
            tool=context.tool,
            data={"status": "consumed"},
        )

    registry.register(
        ToolSpec(
            name="test.produce_purchase_requisition",
            input_model=ProducePurchaseRequisitionInput,
            run=run_produce,
        )
    )
    registry.register(
        ToolSpec(
            name="test.consume_purchase_requisition",
            input_model=ConsumePurchaseRequisitionInput,
            run=run_consume,
        )
    )
    return registry


def _home_reset_registry(*, include_login: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    if include_login:
        registry.register(LOGIN_TOOL)

    def run_tool(context, _params: NoInput) -> ToolResult:
        return ToolResult(
            task_id=context.record.task_id,
            session_id=context.record.session_id,
            tool=context.record.tool,
            data={"status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    return registry


def test_executor_rejects_unknown_tools():
    executor = TraceExecutor()
    record = TraceRecord(
        task_id="task-1",
        session_id="session-1",
        user_id="user-1",
        tool="missing.tool",
        input={},
        line_number=1,
    )

    with pytest.raises(UnknownToolError, match=r"missing\.tool"):
        executor.execute([record], context_factory=lambda item: item)


def test_executor_reports_tool_input_validation_errors():
    executor = TraceExecutor()
    record = TraceRecord(
        task_id="task-1",
        session_id="session-1",
        user_id="user-1",
        tool="fiori.create_purchase_requisition",
        input=_purchase_requisition_input(quantity=0),
        line_number=1,
    )

    with pytest.raises(ToolInputValidationError, match="line 1"):
        executor.execute([record], context_factory=lambda item: item)


def test_executor_resolves_process_scoped_state_variables_before_validation():
    captured_inputs: list[ConsumePurchaseRequisitionInput] = []
    records = [
        TraceRecord(
            task_id="C042_A1",
            session_id="buyer-session",
            user_id="buyer-a",
            tool="test.produce_purchase_requisition",
            input={"pr_number": "10000030"},
            meta={"case_id": "P2P_C042"},
            line_number=1,
        ),
        TraceRecord(
            task_id="C042_A2",
            session_id="buyer-session",
            user_id="buyer-a",
            tool="test.consume_purchase_requisition",
            input={"purchase_requisition": "$purchase_requisition.pr_number"},
            meta={"case_id": "P2P_C042"},
            line_number=2,
        ),
    ]

    executor = TraceExecutor(registry=_state_test_registry(captured_inputs))
    results = executor.execute(records, context_factory=lambda record: record)

    assert [result.tool for result in results] == [
        "test.produce_purchase_requisition",
        "test.consume_purchase_requisition",
    ]
    assert captured_inputs[0].purchase_requisition == "10000030"


def test_executor_fails_unresolved_state_variable_before_context_creation():
    record = TraceRecord(
        task_id="C042_A2",
        session_id="buyer-session",
        user_id="buyer-a",
        tool="test.consume_purchase_requisition",
        input={"purchase_requisition": "$purchase_requisition.pr_number"},
        meta={"case_id": "P2P_C042"},
        line_number=1,
    )

    executor = TraceExecutor(registry=_state_test_registry())

    with pytest.raises(StateResolutionError, match="C042_A2"):
        executor.execute([record], context_factory=lambda _record: pytest.fail("context should not be created"))


def test_executor_fails_state_variable_without_case_id_before_context_creation():
    record = TraceRecord(
        task_id="C042_A2",
        session_id="buyer-session",
        user_id="buyer-a",
        tool="test.consume_purchase_requisition",
        input={"purchase_requisition": "$purchase_requisition.pr_number"},
        line_number=1,
    )

    executor = TraceExecutor(registry=_state_test_registry())

    with pytest.raises(StateResolutionError, match="missing case_id"):
        executor.execute([record], context_factory=lambda _record: pytest.fail("context should not be created"))


def test_executor_clicks_sap_home_logo_twice_after_successful_fiori_tool():
    page = FakeHomeResetPage(logo_click_succeeds=True)
    record = TraceRecord(
        task_id="task-1",
        session_id="session-1",
        user_id="user-1",
        tool="fiori.fake_tool",
        input={},
        line_number=1,
    )

    TraceExecutor(registry=_home_reset_registry()).execute(
        [record],
        context_factory=lambda item: FakeHomeResetContext(item, page),
    )

    assert page.logo_click_count == 2
    assert page.goto_urls == []


def test_executor_attaches_fiori_messages_to_tool_result():
    registry = ToolRegistry()

    def run_tool(context, _params: NoInput) -> ToolResult:
        context.get_browser_session().fiori_messages.append(
            {
                "severity": "error",
                "text": "Geben Sie ein Rechnungsdatum ein.",
                "source": "sap-message-popover",
                "url": "https://sap.example.test/invoice",
            }
        )
        return ToolResult(
            task_id=context.record.task_id,
            session_id=context.record.session_id,
            tool=context.record.tool,
            data={"status": "done"},
        )

    registry.register(ToolSpec(name="fiori.fake_tool", input_model=NoInput, run=run_tool))
    record = TraceRecord(
        task_id="task-1",
        session_id="session-1",
        user_id="user-1",
        tool="fiori.fake_tool",
        input={},
        line_number=1,
    )
    context = FakeMessageContext(record)

    result = TraceExecutor(registry=registry).execute([record], context_factory=lambda _record: context)[0]

    assert result.data["sap_messages"] == [
        {
            "severity": "error",
            "text": "Geben Sie ein Rechnungsdatum ein.",
            "source": "sap-message-popover",
            "url": "https://sap.example.test/invoice",
        }
    ]
    assert context.session.fiori_messages == []


def test_executor_falls_back_to_current_login_url_when_home_logo_clicks_fail():
    page = FakeHomeResetPage(logo_click_succeeds=False)
    trace = [
        TraceRecord(
            task_id="login-1",
            session_id="session-1",
            user_id="user-1",
            tool="fiori.login",
            input={
                "url": "https://sap.example.test/home",
                "username": "user-1",
                "password": "secret",
                "username_selector": "#username",
                "password_selector": "#password",
                "submit_selector": "#login",
            },
            line_number=1,
        ),
        TraceRecord(
            task_id="task-1",
            session_id="session-1",
            user_id="user-1",
            tool="fiori.fake_tool",
            input={},
            line_number=2,
        ),
    ]

    TraceExecutor(registry=_home_reset_registry(include_login=True)).execute(
        trace,
        context_factory=lambda item: FakeHomeResetContext(item, page),
    )

    assert page.logo_click_count == 2
    assert page.goto_urls == [
        "https://sap.example.test/home",
        "https://sap.example.test/current",
    ]


class FakeHomeResetContext:
    def __init__(self, record: TraceRecord, page: "FakeHomeResetPage") -> None:
        self.record = record
        self._session = SimpleNamespace(page=page)

    def get_browser_session(self):
        return self._session


class FakeMessageContext:
    def __init__(self, record: TraceRecord) -> None:
        self.record = record
        self.session = SimpleNamespace(
            page=FakeHomeResetPage(logo_click_succeeds=True),
            fiori_messages=[],
        )

    def get_browser_session(self):
        return self.session


class FakeHomeResetPage:
    url = "https://sap.example.test/current"

    def __init__(self, *, logo_click_succeeds: bool) -> None:
        self.logo_click_succeeds = logo_click_succeeds
        self.logo_click_count = 0
        self.goto_urls: list[str] = []

    def goto(self, url: str) -> None:
        self.goto_urls.append(url)

    def locator(self, selector: str):
        return FakeHomeResetLocator(self, selector)

    def wait_for_load_state(self, _state: str, *, timeout: int | None = None) -> None:
        return None


class FakeHomeResetLocator:
    def __init__(self, page: FakeHomeResetPage, selector: str) -> None:
        self._page = page
        self._selector = selector

    def fill(self, _value: str) -> None:
        return None

    def click(self, *, timeout: int | None = None) -> None:
        if self._selector == "#login":
            return None
        if self._selector == "#shell-header-icon":
            self._page.logo_click_count += 1
            if self._page.logo_click_succeeds:
                return None
        raise PlaywrightError(f"cannot click {self._selector}")

    def is_visible(self) -> bool:
        return False


def test_browser_session_manager_reuses_session_ids():
    with BrowserSessionManager() as session_manager:
        first = session_manager.get_session(session_id="session-1", user_id="user-1")
        second = session_manager.get_session(session_id="session-1", user_id="user-1")
        other = session_manager.get_session(session_id="session-2", user_id="user-1")

        assert first is second
        assert other is not first
        assert session_manager.active_session_count() == 2


def test_browser_session_manager_rejects_mixed_users_for_same_session():
    with BrowserSessionManager() as session_manager:
        session_manager.get_session(session_id="session-1", user_id="user-1")

        with pytest.raises(SessionUserMismatchError, match="session-1"):
            session_manager.get_session(session_id="session-1", user_id="user-2")


def test_executor_runs_login_then_purchase_requisition_against_fixture_app(fixture_app_url):
    records = [
        TraceRecord(
            task_id="task-1",
            session_id="session-1",
            user_id="buyer-a",
            tool="fiori.login",
            input={
                "base_url": fixture_app_url,
                "username": "buyer-a",
                "password": "secret",
                "username_selector": '[data-testid="username"]',
                "password_selector": '[data-testid="password"]',
                "submit_selector": '[data-testid="login-submit"]',
                "success_selector": '[data-testid="session-user"]',
            },
            line_number=1,
        ),
        TraceRecord(
            task_id="task-2",
            session_id="session-1",
            user_id="buyer-a",
            tool="fiori.create_purchase_requisition",
            input=_purchase_requisition_input(quantity=3),
            line_number=2,
        ),
    ]

    executor = TraceExecutor()
    with BrowserSessionManager() as session_manager:
        results = executor.execute(
            records,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
        )

        assert session_manager.active_session_count() == 1

    assert [result.tool for result in results] == ["fiori.login", "fiori.create_purchase_requisition"]
    assert results[0].data["status"] == "logged_in"
    assert results[1].data["status"] == "created"
    assert results[1].data["purchase_requisition"] == "PR-0001"


def test_executor_runs_init_logins_before_tasks_against_fixture_app(fixture_app_url):
    trace = TraceDefinition(
        init=TraceInitRecord(
            line_number=1,
            users=[
                TraceInitUser(
                    session_id="buyer-session",
                    user_id="buyer-a",
                    username="buyer-a",
                    password="secret",
                    login_url=fixture_app_url,
                    username_selector='[data-testid="username"]',
                    password_selector='[data-testid="password"]',
                    submit_selector='[data-testid="login-submit"]',
                    success_selector='[data-testid="session-user"]',
                ),
                TraceInitUser(
                    session_id="approver-session",
                    user_id="approver-a",
                    username="approver-a",
                    password="secret",
                    login_url=fixture_app_url,
                    username_selector='[data-testid="username"]',
                    password_selector='[data-testid="password"]',
                    submit_selector='[data-testid="login-submit"]',
                    success_selector='[data-testid="session-user"]',
                ),
            ],
        ),
        tasks=[
            TraceRecord(
                task_id="task-1",
                session_id="buyer-session",
                user_id="buyer-a",
                tool="fiori.create_purchase_requisition",
                input=_purchase_requisition_input(quantity=3),
                line_number=2,
            ),
            TraceRecord(
                task_id="task-2",
                session_id="approver-session",
                user_id="approver-a",
                tool="fiori.create_purchase_requisition",
                input=_purchase_requisition_input(material="GEAR1000", quantity=1),
                line_number=3,
            ),
        ],
    )

    executor = TraceExecutor()
    with BrowserSessionManager() as session_manager:
        results = executor.execute(
            trace,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
        )

        assert session_manager.active_session_count() == 2

    assert [result.tool for result in results] == [
        "fiori.login",
        "fiori.login",
        "fiori.create_purchase_requisition",
        "fiori.create_purchase_requisition",
    ]
    assert results[0].data["username"] == "buyer-a"
    assert results[1].data["username"] == "approver-a"
    assert results[2].data["purchase_requisition"] == "PR-0001"
    assert results[2].data["quantity"] == 3
    assert results[3].data["purchase_requisition"] == "PR-0001"
    assert results[3].data["quantity"] == 1


def test_executor_resolves_init_passwords_from_credentials_against_fixture_app(fixture_app_url):
    trace = TraceDefinition(
        init=TraceInitRecord(
            line_number=1,
            users=[
                TraceInitUser(
                    session_id="buyer-session",
                    user_id="buyer-a",
                    username="buyer-a",
                    login_url=fixture_app_url,
                    username_selector='[data-testid="username"]',
                    password_selector='[data-testid="password"]',
                    submit_selector='[data-testid="login-submit"]',
                    success_selector='[data-testid="session-user"]',
                )
            ],
        ),
        tasks=[
            TraceRecord(
                task_id="task-1",
                session_id="buyer-session",
                user_id="buyer-a",
                tool="fiori.create_purchase_requisition",
                input=_purchase_requisition_input(quantity=1),
                line_number=2,
            )
        ],
    )

    executor = TraceExecutor(credential_store=EnvCredentialStore({"buyer-a": "secret"}))
    with BrowserSessionManager() as session_manager:
        results = executor.execute(
            trace,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
        )

    assert results[0].data["username"] == "buyer-a"
    assert results[1].data["purchase_requisition"] == "PR-0001"


def test_executor_creates_purchase_requisition_against_fixture_app(fixture_app_url):
    trace = TraceDefinition(
        init=TraceInitRecord(
            line_number=1,
            users=[
                TraceInitUser(
                    session_id="buyer-session",
                    user_id="buyer-a",
                    username="buyer-a",
                    password="secret",
                    login_url=fixture_app_url,
                    username_selector='[data-testid="username"]',
                    password_selector='[data-testid="password"]',
                    submit_selector='[data-testid="login-submit"]',
                    success_selector='[data-testid="session-user"]',
                )
            ],
        ),
        tasks=[
            TraceRecord(
                task_id="task-1",
                session_id="buyer-session",
                user_id="buyer-a",
                tool="fiori.create_purchase_requisition",
                input=_purchase_requisition_input(),
                line_number=2,
            )
        ],
    )

    executor = TraceExecutor()
    with BrowserSessionManager() as session_manager:
        results = executor.execute(
            trace,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
        )

    assert results[1].tool == "fiori.create_purchase_requisition"
    assert results[1].data["status"] == "created"
    assert results[1].data["purchase_requisition"] == "PR-0001"
    assert results[1].data["material"] == "PUMP1902"
    assert results[1].data["quantity"] == 20
    assert results[1].data["returned_objects"] == [
        {
            "object_type": "purchase_requisition",
            "keys": {
                "pr_number": "PR-0001",
            },
        }
    ]


def test_executor_rejects_uninitialized_task_sessions_before_login():
    trace = TraceDefinition(
        init=TraceInitRecord(
            line_number=1,
            users=[
                TraceInitUser(
                    session_id="buyer-session",
                    user_id="buyer-a",
                    username="buyer-a",
                    password="secret",
                )
            ],
        ),
        tasks=[
            TraceRecord(
                task_id="task-1",
                session_id="missing-session",
                user_id="buyer-a",
                tool="fiori.create_purchase_requisition",
                input={},
                line_number=2,
            )
        ],
    )

    executor = TraceExecutor()

    with pytest.raises(SessionUserMismatchError, match="missing-session"):
        executor.execute(trace, context_factory=lambda _record: pytest.fail("context should not be created"))


def test_executor_rejects_initialized_session_user_mismatch_before_login():
    trace = TraceDefinition(
        init=TraceInitRecord(
            line_number=1,
            users=[
                TraceInitUser(
                    session_id="buyer-session",
                    user_id="buyer-a",
                    username="buyer-a",
                    password="secret",
                )
            ],
        ),
        tasks=[
            TraceRecord(
                task_id="task-1",
                session_id="buyer-session",
                user_id="approver-a",
                tool="fiori.create_purchase_requisition",
                input={},
                line_number=2,
            )
        ],
    )

    executor = TraceExecutor()

    with pytest.raises(SessionUserMismatchError, match="approver-a"):
        executor.execute(trace, context_factory=lambda _record: pytest.fail("context should not be created"))
