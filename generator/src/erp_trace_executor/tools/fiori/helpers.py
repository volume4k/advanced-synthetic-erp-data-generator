"""Shared helpers for SAP Fiori tools."""

from __future__ import annotations

from math import isclose
from typing import Callable


def format_number(value: float) -> str:
    rounded = round(value)
    if isclose(value, rounded, rel_tol=0.0, abs_tol=1e-9):
        return str(int(rounded))
    return str(value)


RuntimeDelay = Callable[[str, float], None]


def runtime_delay_callback(context) -> RuntimeDelay:
    def delay(marker: str, base_seconds: float) -> None:
        runtime_delay_marker = getattr(context, "runtime_delay_marker", None)
        if callable(runtime_delay_marker):
            runtime_delay_marker(marker, base_seconds)

    return delay


def noop_delay(_marker: str, _base_seconds: float) -> None:
    return None
