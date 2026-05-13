"""Core data models used by the trace executor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def returned_object(object_type: str, **keys: Any) -> dict[str, Any]:
    """Build one generated SAP object entry for a tool result."""

    if not object_type:
        raise ValueError("object_type must not be empty")
    if not keys:
        raise ValueError("returned object keys must not be empty")

    return {
        "object_type": object_type,
        "keys": keys,
    }


class ExecutionTaskRecord(BaseModel):
    """One planned node execution record."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    session_id: str
    user_id: str
    tool: str
    input: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
    line_number: int


class SessionInitUser(BaseModel):
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


class SessionInitRecord(BaseModel):
    """Browser session logins required before node execution."""

    model_config = ConfigDict(extra="forbid")

    users: list[SessionInitUser] = Field(min_length=1)
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
