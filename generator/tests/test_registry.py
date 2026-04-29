from __future__ import annotations

import pytest
from pydantic import BaseModel

from erp_trace_executor.errors import DuplicateToolRegistrationError, UnknownToolError
from erp_trace_executor.registry import ToolRegistry
from erp_trace_executor.tooling import ToolSpec


class StubInput(BaseModel):
    value: str


def _run(_context, _params):
    raise AssertionError("not executed")


def test_registry_rejects_duplicate_tool_names():
    registry = ToolRegistry()
    spec = ToolSpec(name="stub.tool", input_model=StubInput, run=_run)

    registry.register(spec)

    with pytest.raises(DuplicateToolRegistrationError, match="stub.tool"):
        registry.register(spec)


def test_registry_rejects_unknown_tool_names():
    registry = ToolRegistry()

    with pytest.raises(UnknownToolError, match="missing.tool"):
        registry.get("missing.tool")


def test_default_registry_includes_purchase_requisition_tool():
    from erp_trace_executor.registry import build_default_registry

    registry = build_default_registry()

    assert "fiori.create_purchase_requisition" in registry.names()
