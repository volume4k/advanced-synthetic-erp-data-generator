"""Core data models used by the trace executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class TraceTaskRecord(BaseModel):
    """One JSONL task record."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    session_id: str
    user_id: str
    tool: str
    input: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
    line_number: int


TraceRecord = TraceTaskRecord


class TraceInitUser(BaseModel):
    """One browser user to log in before task execution."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    user_id: str
    username: str
    password: str | None = None
    login_url: HttpUrl | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_selector: str | None = None


class TraceInitRecord(BaseModel):
    """Optional first JSONL record for pre-task browser logins."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["init"] = "init"
    users: list[TraceInitUser] = Field(min_length=1)
    line_number: int


class TraceDefinition(BaseModel):
    """Parsed trace with optional init and ordered task records."""

    model_config = ConfigDict(extra="forbid")

    init: TraceInitRecord | None = None
    tasks: list[TraceTaskRecord] = Field(default_factory=list)


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
