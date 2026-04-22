"""Sequential trace execution."""

from __future__ import annotations

from pydantic import ValidationError

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import ToolInputValidationError
from erp_trace_executor.models import ToolResult, TraceRecord
from erp_trace_executor.registry import ToolRegistry, build_default_registry


class TraceExecutor:
    """Executes validated trace records in file order."""

    def __init__(self, *, registry: ToolRegistry | None = None) -> None:
        self._registry = registry or build_default_registry()

    def execute(self, records: list[TraceRecord], context_factory) -> list[ToolResult]:
        results: list[ToolResult] = []

        for record in records:
            spec = self._registry.get(record.tool)
            try:
                params = spec.input_model.model_validate(record.input)
            except ValidationError as exc:
                raise ToolInputValidationError(
                    f"Invalid input for tool '{record.tool}' on line {record.line_number}: {exc}"
                ) from exc

            context = context_factory(record)
            results.append(spec.run(context, params))

        return results

    @property
    def registry(self) -> ToolRegistry:
        return self._registry
