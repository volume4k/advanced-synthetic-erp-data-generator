"""Runtime delay markers shared by executor contexts and browser tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

DEFAULT_ACTION_DELAY_MIN_SECONDS = 0.12
DEFAULT_ACTION_DELAY_MAX_SECONDS = 0.35


@dataclass(frozen=True)
class RuntimeDelayBounds:
    """Optional marker-local runtime delay bounds, in seconds."""

    min_seconds: float | None = None
    max_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.min_seconds is not None and self.min_seconds < 0:
            raise ValueError("min_seconds must be greater than or equal to 0")
        if self.max_seconds is not None and self.max_seconds < 0:
            raise ValueError("max_seconds must be greater than or equal to 0")
        if (
            self.min_seconds is not None
            and self.max_seconds is not None
            and self.min_seconds > self.max_seconds
        ):
            raise ValueError("min_seconds must be <= max_seconds")


class RuntimeDelay(Protocol):
    def __call__(
        self,
        marker: str,
        base_seconds: float,
        bounds: RuntimeDelayBounds | None = None,
    ) -> None: ...


class RuntimeActionDelay(Protocol):
    def __call__(self, action: str) -> None: ...


def runtime_delay_callback(context) -> RuntimeDelay:
    def delay(marker: str, base_seconds: float, bounds: RuntimeDelayBounds | None = None) -> None:
        runtime_delay_marker = getattr(context, "runtime_delay_marker", None)
        if callable(runtime_delay_marker):
            runtime_delay_marker(marker, base_seconds, bounds)

    return delay


def runtime_action_delay_callback(context) -> RuntimeActionDelay:
    def delay(action: str) -> None:
        runtime_action_delay = getattr(context, "runtime_action_delay", None)
        if callable(runtime_action_delay):
            runtime_action_delay(action)

    return delay


def noop_delay(
    _marker: str,
    _base_seconds: float,
    _bounds: RuntimeDelayBounds | None = None,
) -> None:
    return None


def noop_action_delay(_action: str) -> None:
    return None
