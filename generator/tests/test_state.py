from __future__ import annotations

import pytest

from erp_trace_executor.errors import StateResolutionError
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.state import RuntimeStateStore


def _purchase_requisition_result() -> ToolResult:
    return ToolResult(
        task_id="C042_A1",
        session_id="buyer-session",
        tool="fiori.create_purchase_requisition",
        data={
            "returned_objects": [
                returned_object("purchase_requisition", pr_number="10000030")
            ],
        },
    )


def test_runtime_state_records_returned_object_and_resolves_key():
    state = RuntimeStateStore()

    state.record_tool_result("P2P_C042", "C042_A1", _purchase_requisition_result())

    assert state.resolve("P2P_C042", "$purchase_requisition.pr_number", task_id="C042_A2") == "10000030"


@pytest.mark.parametrize(
    ("case_id", "variable", "match"),
    [
        (None, "$purchase_requisition.pr_number", "missing case_id"),
        ("P2P_C042", "purchase_requisition.pr_number", "must start"),
        ("P2P_C042", "$purchase_requisition", "expected"),
        ("P2P_C042", "$purchase_order.po_number", "object 'purchase_order' not found"),
        ("P2P_C042", "$purchase_requisition.po_number", "key 'po_number' not found"),
    ],
)
def test_runtime_state_reports_missing_or_invalid_variables(case_id: str | None, variable: str, match: str):
    state = RuntimeStateStore()
    state.record_tool_result("P2P_C042", "C042_A1", _purchase_requisition_result())

    with pytest.raises(StateResolutionError, match=match):
        state.resolve(case_id, variable, task_id="C042_A2")


def test_runtime_state_rejects_duplicate_object_type_in_case():
    state = RuntimeStateStore()
    state.record_tool_result("P2P_C042", "C042_A1", _purchase_requisition_result())

    with pytest.raises(StateResolutionError, match="already exists"):
        state.record_tool_result("P2P_C042", "C042_A1_retry", _purchase_requisition_result())


def test_runtime_state_fails_missing_item_key_when_tool_did_not_return_it():
    state = RuntimeStateStore()
    state.record_tool_result(
        "P2P_C042",
        "C042_A2",
        ToolResult(
            task_id="C042_A2",
            session_id="buyer-session",
            tool="fiori.create_purchase_order",
            data={
                "returned_objects": [
                    returned_object("purchase_order", po_number="4500008732")
                ],
            },
        ),
    )

    with pytest.raises(StateResolutionError, match="key 'po_item' not found"):
        state.resolve("P2P_C042", "$purchase_order.po_item", task_id="C042_A3")
