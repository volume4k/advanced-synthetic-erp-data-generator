"""Shared helpers for SAP Fiori tools."""

from __future__ import annotations

from math import isclose

from erp_trace_executor.runtime_delay import (
    RuntimeDelay,
    RuntimeDelayBounds,
    noop_delay,
    runtime_delay_callback,
)


def format_number(value: float) -> str:
    rounded = round(value)
    if isclose(value, rounded, rel_tol=0.0, abs_tol=1e-9):
        return str(int(rounded))
    return str(value)


__all__ = [
    "RuntimeDelay",
    "RuntimeDelayBounds",
    "format_number",
    "noop_delay",
    "runtime_delay_callback",
]
