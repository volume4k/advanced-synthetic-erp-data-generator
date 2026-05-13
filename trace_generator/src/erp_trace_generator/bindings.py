"""Resolve step-local Pkl input bindings into tool inputs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import CasePlan, InputBinding, ProcessStep


def resolve_step_inputs(step: ProcessStep, case: CasePlan) -> dict[str, Any]:
    return {binding.field: _resolve_binding(binding, case) for binding in step.input_bindings}


def business_dates_for_step(step: ProcessStep, case: CasePlan) -> dict[str, str]:
    dates: dict[str, str] = {}
    for binding in step.input_bindings:
        if binding.source != "derived" and binding.source != "business_date":
            continue
        if "delivery_date" in binding.value and "date" in binding.field:
            dates[binding.field] = case.delivery_date.isoformat()
        if "payment_posting_date" in binding.value and "date" in binding.field:
            dates[binding.field] = (case.delivery_date + timedelta(days=1)).isoformat()
    return dates


def _resolve_binding(binding: InputBinding, case: CasePlan) -> Any:
    if binding.source == "literal":
        return _cast_literal(binding.value, binding.value_type)
    if binding.source == "prior_output":
        return f"${binding.value}"
    if binding.source == "master_data":
        return _case_value(case, binding.value)
    if binding.source == "case":
        return _case_value(case, binding.value)
    if binding.source == "business_date":
        return _business_date_value(case, binding.value)
    if binding.source == "derived":
        return _derived_value(case, binding.value)
    raise TraceGenerationError(f"unsupported binding source '{binding.source}'")


def _case_value(case: CasePlan, value: str) -> Any:
    attr = _CASE_VALUE_ALIASES.get(value, value)
    if not hasattr(case, attr):
        raise TraceGenerationError(f"Unknown case binding value '{value}'")
    return getattr(case, attr)


def _business_date_value(case: CasePlan, value: str) -> str:
    if value == "delivery_date":
        return case.delivery_date.isoformat()
    if value == "payment_posting_date":
        return (case.delivery_date + timedelta(days=1)).isoformat()
    raise TraceGenerationError(f"Unknown business_date binding value '{value}'")


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
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        return value.lower() == "true"
    raise TraceGenerationError(f"unsupported binding valueType '{value_type}'")


def _fiori_date(value) -> str:
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
    "currency": "currency",
}
