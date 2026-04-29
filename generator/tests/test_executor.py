from __future__ import annotations

import pytest

from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import EnvCredentialStore
from erp_trace_executor.errors import SessionUserMismatchError, ToolInputValidationError, UnknownToolError
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.models import TraceDefinition, TraceInitRecord, TraceInitUser, TraceRecord


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

    with pytest.raises(UnknownToolError, match="missing.tool"):
        executor.execute([record], context_factory=lambda item: item)


def test_executor_reports_tool_input_validation_errors():
    executor = TraceExecutor()
    record = TraceRecord(
        task_id="task-1",
        session_id="session-1",
        user_id="user-1",
        tool="fiori.create_order",
        input={"item_name": "widget", "quantity": 0},
        line_number=1,
    )

    with pytest.raises(ToolInputValidationError, match="line 1"):
        executor.execute([record], context_factory=lambda item: item)


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


def test_executor_runs_login_then_order_against_fixture_app(fixture_app_url):
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
            tool="fiori.create_order",
            input={
                "item_name": "widget",
                "quantity": 3,
            },
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

    assert [result.tool for result in results] == ["fiori.login", "fiori.create_order"]
    assert results[0].data["status"] == "logged_in"
    assert results[1].data["latest_order"] == "widget:3"
    assert results[1].data["order_count"] == 1


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
                tool="fiori.create_order",
                input={
                    "item_name": "widget",
                    "quantity": 3,
                },
                line_number=2,
            ),
            TraceRecord(
                task_id="task-2",
                session_id="approver-session",
                user_id="approver-a",
                tool="fiori.create_order",
                input={
                    "item_name": "gadget",
                    "quantity": 1,
                },
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
        "fiori.create_order",
        "fiori.create_order",
    ]
    assert results[0].data["username"] == "buyer-a"
    assert results[1].data["username"] == "approver-a"
    assert results[2].data["latest_order"] == "widget:3"
    assert results[2].data["order_count"] == 1
    assert results[3].data["latest_order"] == "gadget:1"
    assert results[3].data["order_count"] == 1


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
                tool="fiori.create_order",
                input={"item_name": "widget", "quantity": 1},
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
    assert results[1].data["latest_order"] == "widget:1"


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
                tool="fiori.create_order",
                input={"item_name": "widget", "quantity": 1},
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
                tool="fiori.create_order",
                input={"item_name": "widget", "quantity": 1},
                line_number=2,
            )
        ],
    )

    executor = TraceExecutor()

    with pytest.raises(SessionUserMismatchError, match="approver-a"):
        executor.execute(trace, context_factory=lambda _record: pytest.fail("context should not be created"))
