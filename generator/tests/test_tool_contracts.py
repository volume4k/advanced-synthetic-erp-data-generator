from __future__ import annotations

import inspect
from pathlib import Path

from pydantic import BaseModel

from erp_trace_executor.registry import build_default_registry
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.trace_loader import load_trace_records

EXAMPLES_DIR = Path(__file__).parents[1] / "examples"
INFRASTRUCTURE_TOOLS = {"fiori.login"}


def test_default_registry_tool_specs_satisfy_core_contract():
    registry = build_default_registry()
    names = registry.names()

    assert names
    assert len(names) == len(set(names))

    for name in names:
        spec = registry.get(name)
        signature = inspect.signature(spec.run)

        assert isinstance(spec, ToolSpec)
        assert spec.name == name
        assert issubclass(spec.input_model, BaseModel)
        assert callable(spec.run)
        assert len(signature.parameters) == 2


def test_registered_business_tools_have_valid_example_trace_inputs():
    registry = build_default_registry()
    seen_tools: set[str] = set()

    for trace_path in sorted(EXAMPLES_DIR.glob("*.trace.jsonl")):
        trace = load_trace_records(trace_path)
        for record in trace.tasks:
            spec = registry.get(record.tool)
            spec.input_model.model_validate(record.input)
            seen_tools.add(record.tool)

    business_tools = set(registry.names()) - INFRASTRUCTURE_TOOLS
    missing_examples = business_tools - seen_tools

    assert missing_examples == set()
