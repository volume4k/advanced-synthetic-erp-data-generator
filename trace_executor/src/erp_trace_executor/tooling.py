"""Tool registration types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

from pydantic import BaseModel

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult

InputModelT = TypeVar("InputModelT", bound=BaseModel)


@dataclass(frozen=True)
class ToolSpec(Generic[InputModelT]):
    """Specification for one executable tool."""

    name: str
    input_model: type[InputModelT]
    run: Callable[[ExecutionContext, InputModelT], ToolResult]
