"""Fraud graph-transform extension points."""

from __future__ import annotations

from collections.abc import Callable

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import FraudScenario

FraudTransformer = Callable[[object], object]

FRAUD_TRANSFORMERS: dict[str, FraudTransformer] = {}


def ensure_fraud_scenarios_supported(scenarios: tuple[FraudScenario, ...]) -> None:
    for scenario in scenarios:
        if scenario.enabled and scenario.id not in FRAUD_TRANSFORMERS:
            raise TraceGenerationError(f"No graph transformer registered for enabled fraud scenario '{scenario.id}'")
