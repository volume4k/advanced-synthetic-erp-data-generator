"""Shared helpers for SAP Fiori tools."""

from __future__ import annotations

from math import isclose


def format_number(value: float) -> str:
    rounded = round(value)
    if isclose(value, rounded, rel_tol=0.0, abs_tol=1e-9):
        return str(int(rounded))
    return str(value)
