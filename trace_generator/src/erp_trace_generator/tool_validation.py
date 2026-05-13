"""Validate generated tool inputs against current executor tool schemas."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import PlannedNode


def validate_node_tool_inputs(nodes: list[PlannedNode]) -> None:
    registry = _build_executor_registry()
    for node in nodes:
        try:
            registry.get(node.tool_name).input_model.model_validate(node.inputs)
        except ValidationError as exc:
            raise TraceGenerationError(f"Invalid input for tool '{node.tool_name}' on node '{node.node_id}': {exc}") from exc


def _build_executor_registry():
    repo_root = Path(__file__).resolve().parents[3]
    generator_src = repo_root / "generator" / "src"
    if str(generator_src) not in sys.path:
        sys.path.insert(0, str(generator_src))
    try:
        from erp_trace_executor.registry import build_default_registry
    except ImportError as exc:
        raise TraceGenerationError(
            "Cannot import generator tool registry for input validation; run from repository checkout"
        ) from exc
    return build_default_registry()
