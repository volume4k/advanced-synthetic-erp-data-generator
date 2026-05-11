from __future__ import annotations

import textwrap

import pytest

from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.trace_loader import load_trace_records


def test_load_trace_records_preserves_order(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        textwrap.dedent(
            """
            {"task_id":"task-1","session_id":"session-1","user_id":"user-1","tool":"fiori.login","input":{"base_url":"http://127.0.0.1:8000","username":"user-1","password":"secret"}}
            {"task_id":"task-2","session_id":"session-1","user_id":"user-1","tool":"fiori.create_purchase_requisition","input":{}}
            """
        ).strip(),
        encoding="utf-8",
    )

    trace = load_trace_records(trace_path)

    assert trace.init is None
    assert [record.task_id for record in trace.tasks] == ["task-1", "task-2"]
    assert [record.line_number for record in trace.tasks] == [1, 2]


def test_load_trace_records_accepts_first_init_record(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        textwrap.dedent(
            """
            {"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"BUYERA","password":"secret"},{"session_id":"approver-session","user_id":"approver-a","username":"APPROVERA","password":"secret"}]}
            {"task_id":"task-1","session_id":"buyer-session","user_id":"buyer-a","tool":"fiori.create_purchase_requisition","input":{}}
            """
        ).strip(),
        encoding="utf-8",
    )

    trace = load_trace_records(trace_path)

    assert trace.init is not None
    assert [user.session_id for user in trace.init.users] == ["buyer-session", "approver-session"]
    assert [user.user_id for user in trace.init.users] == ["buyer-a", "approver-a"]
    assert [record.task_id for record in trace.tasks] == ["task-1"]


def test_load_trace_records_accepts_init_users_without_password(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"BUYERA"}]}',
        encoding="utf-8",
    )

    trace = load_trace_records(trace_path)

    assert trace.init is not None
    assert trace.init.users[0].username == "BUYERA"
    assert trace.init.users[0].password is None


def test_load_trace_records_rejects_late_init_record(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        textwrap.dedent(
            """
            {"task_id":"task-1","session_id":"buyer-session","user_id":"buyer-a","tool":"fiori.create_purchase_requisition","input":{}}
            {"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"BUYERA","password":"secret"}]}
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Init record must be the first trace record"):
        load_trace_records(trace_path)


def test_load_trace_records_rejects_duplicate_init_records(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        textwrap.dedent(
            """
            {"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"BUYERA","password":"secret"}]}
            {"kind":"init","users":[{"session_id":"approver-session","user_id":"approver-a","username":"APPROVERA","password":"secret"}]}
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Only one init record is allowed"):
        load_trace_records(trace_path)


def test_load_trace_records_rejects_duplicate_init_session_ids(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"kind":"init","users":[{"session_id":"shared","user_id":"buyer-a","username":"BUYERA","password":"secret"},'
        '{"session_id":"shared","user_id":"approver-a","username":"APPROVERA","password":"secret"}]}',
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Duplicate init session_id 'shared'"):
        load_trace_records(trace_path)


def test_load_trace_records_rejects_duplicate_init_user_ids(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"kind":"init","users":[{"session_id":"buyer-session","user_id":"shared-user","username":"BUYERA","password":"secret"},'
        '{"session_id":"approver-session","user_id":"shared-user","username":"APPROVERA","password":"secret"}]}',
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Duplicate init user_id 'shared-user'"):
        load_trace_records(trace_path)


def test_load_trace_records_reports_json_line_errors(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text('{"task_id": "task-1"\n', encoding="utf-8")

    with pytest.raises(TraceParseError, match="Invalid JSON on line 1"):
        load_trace_records(trace_path)


def test_load_trace_records_reports_schema_errors(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        '{"task_id":"task-1","session_id":"session-1","user_id":"user-1","tool":"fiori.login"}',
        encoding="utf-8",
    )

    with pytest.raises(TraceParseError, match="Invalid trace record on line 1"):
        load_trace_records(trace_path)

