"""Fraud graph-transform extension points."""

from __future__ import annotations

from collections.abc import Callable

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import FraudScenario

FraudTransformer = Callable[[object], object]

FRAUD_TRANSFORMERS: dict[str, FraudTransformer] = {}


def register_fraud_transformer(scenario_id: str) -> Callable[[FraudTransformer], FraudTransformer]:
    if not scenario_id:
        raise TraceGenerationError("Fraud transformer scenario id must not be empty")

    def decorator(transformer: FraudTransformer) -> FraudTransformer:
        if scenario_id in FRAUD_TRANSFORMERS:
            raise TraceGenerationError(f"Fraud transformer '{scenario_id}' is already registered")
        FRAUD_TRANSFORMERS[scenario_id] = transformer
        return transformer

    return decorator


def ensure_fraud_scenarios_supported(scenarios: tuple[FraudScenario, ...]) -> None:
    for scenario in scenarios:
        if scenario.enabled and scenario.id not in FRAUD_TRANSFORMERS:
            raise TraceGenerationError(f"No graph transformer registered for enabled fraud scenario '{scenario.id}'")
