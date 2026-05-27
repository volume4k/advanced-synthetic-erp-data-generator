"""Resolve step-local Pkl input bindings into tool inputs."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import CasePlan, InputBinding, ProcessStep


def resolve_step_inputs(step: ProcessStep, case: CasePlan) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for binding in step.input_bindings:
        _set_binding_value(inputs, binding.field, _resolve_binding(binding, case))
    return inputs


def _set_binding_value(inputs: dict[str, Any], field: str, value: Any) -> None:
    parts = field.split(".")
    target = inputs
    for part in parts[:-1]:
        existing = target.setdefault(part, {})
        if not isinstance(existing, dict):
            raise TraceGenerationError(f"Cannot bind nested field '{field}': '{part}' already has a scalar value")
        target = existing
    leaf = parts[-1]
    if leaf in target:
        raise TraceGenerationError(f"Duplicate binding for field '{field}'")
    target[leaf] = value


def planned_date_inputs_for_step(step: ProcessStep, case: CasePlan) -> dict[str, str]:
    return {
        binding.field: _planned_date_input_binding_value(binding, case)
        for binding in step.planned_date_input_bindings
    }


def _resolve_binding(binding: InputBinding, case: CasePlan) -> Any:
    if binding.source == "literal":
        return _cast_literal(binding.value, binding.value_type)
    if binding.source == "prior_output":
        return f"${binding.value}"
    if binding.source == "master_data":
        return _case_value(case, binding.value)
    if binding.source == "case":
        return _case_value(case, binding.value)
    if binding.source == "planned_date":
        return _planned_date_value(case, binding.value)
    if binding.source == "derived":
        return _derived_value(case, binding.value)
    raise TraceGenerationError(f"unsupported binding source '{binding.source}'")


def _case_value(case: CasePlan, value: str) -> Any:
    attr = _CASE_VALUE_ALIASES.get(value, value)
    if not hasattr(case, attr):
        raise TraceGenerationError(f"Unknown case binding value '{value}'")
    return getattr(case, attr)


def _planned_date_value(case: CasePlan, value: str) -> str:
    if value == "delivery_date":
        return case.delivery_date.isoformat()
    if value == "payment_posting_date":
        return (case.delivery_date + timedelta(days=1)).isoformat()
    raise TraceGenerationError(f"Unknown planned_date binding value '{value}'")


def _planned_date_input_binding_value(binding: InputBinding, case: CasePlan) -> str:
    if binding.source == "planned_date":
        return _planned_date_value(case, binding.value)
    if binding.source == "derived":
        if binding.value == "fiori_delivery_date":
            return case.delivery_date.isoformat()
        if binding.value == "fiori_payment_posting_date":
            return (case.delivery_date + timedelta(days=1)).isoformat()
    raise TraceGenerationError(
        f"Business date binding '{binding.field}' uses unsupported source/value: "
        f"{binding.source}.{binding.value}"
    )


def _derived_value(case: CasePlan, value: str) -> Any:
    if value == "gross_amount":
        return case.gross_amount
    if value == "fiori_delivery_date":
        return _fiori_date(case.delivery_date)
    if value == "fiori_payment_posting_date":
        return _fiori_date(case.delivery_date + timedelta(days=1))
    if value == "storage_location_label":
        return case.storage_location_label
    raise TraceGenerationError(f"Unknown derived binding value '{value}'")


def _cast_literal(value: str, value_type: str) -> str | int | float | bool:
    if value_type == "string":
        return value
    if value_type == "int":
        try:
            return int(value)
        except ValueError as exc:
            raise TraceGenerationError(f"Cannot cast literal '{value}' to int") from exc
    if value_type == "float":
        try:
            return float(value)
        except ValueError as exc:
            raise TraceGenerationError(f"Cannot cast literal '{value}' to float") from exc
    if value_type == "bool":
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise TraceGenerationError(f"Cannot cast literal '{value}' to bool")
    raise TraceGenerationError(f"unsupported binding valueType '{value_type}'")


def _fiori_date(value: date | datetime) -> str:
    if not isinstance(value, date):
        raise TraceGenerationError(f"Cannot format non-date value '{value}' as Fiori date")
    return value.strftime("%m/%d/%Y")


_CASE_VALUE_ALIASES = {
    "materialId": "material_id",
    "vendorId": "vendor_id",
    "vendor_id": "vendor_id",
    "plant": "plant",
    "purchasing_org": "purchasing_org",
    "purchasingOrg": "purchasing_org",
    "storage_location": "storage_location",
    "storageLocation": "storage_location",
    "quantity": "quantity",
    "target_price": "target_price",
    "targetPrice": "target_price",
    "gross_amount": "gross_amount",
    "grossAmount": "gross_amount",
    "currency": "currency",
}
