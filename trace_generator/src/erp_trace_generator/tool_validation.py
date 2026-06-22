"""Validate generated tool inputs against current executor tool schemas."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import PlannedStep


def validate_planned_step_tool_inputs(planned_steps: list[PlannedStep]) -> None:
    registry = _build_executor_registry()
    registered_tools = set(registry.names())
    for planned_step in planned_steps:
        if planned_step.tool_name not in registered_tools:
            raise TraceGenerationError(
                f"Tool '{planned_step.tool_name}' is not registered for planned step '{planned_step.planned_step_id}'"
            )
        tool = registry.get(planned_step.tool_name)
        try:
            tool.input_model.model_validate(planned_step.inputs)
        except ValidationError as exc:
            raise TraceGenerationError(
                f"Invalid input for tool '{planned_step.tool_name}' on planned step "
                f"'{planned_step.planned_step_id}': {exc}"
            ) from exc


def _build_executor_registry():
    repo_root = Path(__file__).resolve().parents[3]
    executor_src = repo_root / "trace_executor" / "src"
    if str(executor_src) not in sys.path:
        sys.path.insert(0, str(executor_src))
    try:
        from erp_trace_executor.registry import build_default_registry
    except ImportError as exc:
        raise TraceGenerationError(
            "Cannot import trace executor tool registry for input validation; run from repository checkout"
        ) from exc
    return build_default_registry()
