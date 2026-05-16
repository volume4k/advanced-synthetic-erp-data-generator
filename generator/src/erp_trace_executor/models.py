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
    """One planned-step execution record."""

    model_config = ConfigDict(extra="forbid")

    planned_step_id: str
    actor_session_id: str
    synthetic_actor_id: str
    tool: str
    input: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
    line_number: int


class HumanDelayProfile(BaseModel):
    """Runtime-safe actor delay metadata carried by canonical traces."""

    model_config = ConfigDict(extra="forbid")

    delay_multiplier: float = Field(gt=0)
    runtime_delay_cap_seconds: float = Field(ge=0)


class SessionInitUser(BaseModel):
    """One actor session to log in before planned-step execution."""

    model_config = ConfigDict(extra="forbid")

    actor_session_id: str
    synthetic_actor_id: str
    username: str
    password: str | None = None
    login_url: HttpUrl | None = None
    username_selector: str | None = None
    password_selector: str | None = None
    submit_selector: str | None = None
    success_selector: str | None = None
    human_delay_profile: HumanDelayProfile | None = None


class SessionInitRecord(BaseModel):
    """Actor session logins required before planned-step execution."""

    model_config = ConfigDict(extra="forbid")

    users: list[SessionInitUser] = Field(min_length=1)
    line_number: int


@dataclass(frozen=True)
class ToolResult:
    """Structured output for one tool execution."""

    planned_step_id: str
    actor_session_id: str
    tool: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned_step_id": self.planned_step_id,
            "actor_session_id": self.actor_session_id,
            "tool": self.tool,
            "data": self.data,
        }
