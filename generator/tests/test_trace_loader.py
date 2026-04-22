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
            {"task_id":"task-2","session_id":"session-1","user_id":"user-1","tool":"fiori.create_order","input":{"item_name":"widget","quantity":2}}
            """
        ).strip(),
        encoding="utf-8",
    )

    records = load_trace_records(trace_path)

    assert [record.task_id for record in records] == ["task-1", "task-2"]
    assert [record.line_number for record in records] == [1, 2]


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
