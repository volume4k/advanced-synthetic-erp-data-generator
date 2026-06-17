"""Domain errors for the trace executor."""

from __future__ import annotations


class TraceExecutorError(Exception):
    """Base class for executor failures."""


class TraceParseError(TraceExecutorError):
    """Raised when a trace file cannot be parsed into trace records."""


class UnknownToolError(TraceExecutorError):
    """Raised when a trace references an unregistered tool."""


class DuplicateToolRegistrationError(TraceExecutorError):
    """Raised when the same tool name is registered twice."""


class ToolInputValidationError(TraceExecutorError):
    """Raised when a trace record input does not match a tool schema."""


class SessionUserMismatchError(TraceExecutorError):
    """Raised when a session id is reused for a different user."""


class ToolExecutionError(TraceExecutorError):
    """Raised when a tool cannot complete its browser action."""


class StateResolutionError(TraceExecutorError):
    """Raised when a runtime state variable cannot be resolved."""
