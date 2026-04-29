"""Central tool registry for the executor."""

from __future__ import annotations

from erp_trace_executor.errors import DuplicateToolRegistrationError, UnknownToolError
from erp_trace_executor.tooling import ToolSpec
from erp_trace_executor.tools.fiori.create_order import CREATE_ORDER_TOOL
from erp_trace_executor.tools.fiori.create_purchase_requisition import CREATE_PURCHASE_REQUISITION_TOOL
from erp_trace_executor.tools.fiori.login import LOGIN_TOOL


class ToolRegistry:
    """Explicit registry used by the v1 executor."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise DuplicateToolRegistrationError(f"Tool '{spec.name}' is already registered")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise UnknownToolError(f"Unknown tool '{name}'") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools.keys())


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(LOGIN_TOOL)
    registry.register(CREATE_ORDER_TOOL)
    registry.register(CREATE_PURCHASE_REQUISITION_TOOL)
    return registry
