"""Core data models used by the trace executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TraceRecord(BaseModel):
    """One JSONL task record."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    session_id: str
    user_id: str
    tool: str
    input: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
    line_number: int


@dataclass(frozen=True)
class ToolResult:
    """Structured output for one tool execution."""

    task_id: str
    session_id: str
    tool: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "tool": self.tool,
            "data": self.data,
        }
