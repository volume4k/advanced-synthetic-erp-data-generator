"""Process-scoped runtime state for generated SAP object keys."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from erp_trace_executor.errors import StateResolutionError
from erp_trace_executor.models import ToolResult


@dataclass
class RuntimeObject:
    keys: dict[str, Any]
    source_task_id: str
    tool: str


@dataclass
class RuntimeCaseState:
    objects: dict[str, RuntimeObject] = field(default_factory=dict)


class RuntimeStateStore:
    """Stores generated SAP object keys by process/case id."""

    def __init__(self) -> None:
        self._cases: dict[str, RuntimeCaseState] = {}

    def resolve(self, case_id: str | None, variable: str, *, task_id: str | None = None) -> Any:
        if not case_id:
            raise self._error(task_id, case_id, variable, "missing case_id")
        if not variable.startswith("$"):
            raise self._error(task_id, case_id, variable, "variable must start with '$'")

        path = variable[1:]
        parts = path.split(".")
        if len(parts) != 2 or not all(parts):
            raise self._error(task_id, case_id, variable, "expected '$object.key'")

        object_type, key = parts
        case_state = self._cases.get(case_id)
        if case_state is None:
            raise self._error(task_id, case_id, variable, "case has no runtime state")

        runtime_object = case_state.objects.get(object_type)
        if runtime_object is None:
            raise self._error(task_id, case_id, variable, f"object '{object_type}' not found")

        if key not in runtime_object.keys:
            raise self._error(task_id, case_id, variable, f"key '{key}' not found")

        return runtime_object.keys[key]

    def record_tool_result(self, case_id: str | None, task_id: str, result: ToolResult) -> None:
        if not case_id:
            raise StateResolutionError(f"Cannot record state for task '{task_id}': missing case_id")

        returned_objects = result.data.get("returned_objects", [])
        if not returned_objects:
            return

        case_state = self._cases.setdefault(case_id, RuntimeCaseState())
        for returned_object in returned_objects:
            object_type = returned_object.get("object_type")
            keys = returned_object.get("keys")
            if not isinstance(object_type, str) or not object_type:
                raise StateResolutionError(f"Cannot record state for task '{task_id}': returned object missing object_type")
            if not isinstance(keys, dict):
                raise StateResolutionError(
                    f"Cannot record state for task '{task_id}', object '{object_type}': keys must be an object"
                )
            if object_type in case_state.objects:
                raise StateResolutionError(
                    f"Cannot record state for task '{task_id}', case '{case_id}': object '{object_type}' already exists"
                )

            case_state.objects[object_type] = RuntimeObject(
                keys=dict(keys),
                source_task_id=task_id,
                tool=result.tool,
            )

    def _error(self, task_id: str | None, case_id: str | None, variable: str, reason: str) -> StateResolutionError:
        task = task_id or "<unknown>"
        case = case_id or "<missing>"
        return StateResolutionError(
            f"Cannot resolve variable '{variable}' for task '{task}', case '{case}': {reason}"
        )
