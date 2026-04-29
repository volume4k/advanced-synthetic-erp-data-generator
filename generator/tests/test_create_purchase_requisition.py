from __future__ import annotations

import pytest
from pydantic import ValidationError

from erp_trace_executor.tools.fiori.create_purchase_requisition import CreatePurchaseRequisitionInput


def _valid_payload() -> dict[str, object]:
    return {
        "material": "PUMP1902",
        "quantity": 20,
        "valuation_price": 30,
        "currency": "USD",
        "price_unit": 1,
        "delivery_date": "20.05.2026",
        "plant": "MI00",
        "purchasing_group": "N00",
        "purchasing_organization": "US00",
        "company_code": "US00",
    }


def test_create_purchase_requisition_input_accepts_required_fields():
    params = CreatePurchaseRequisitionInput.model_validate(_valid_payload())

    assert params.material == "PUMP1902"
    assert params.quantity == 20
    assert params.valuation_price == 30
    assert params.currency == "USD"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0),
        ("valuation_price", 0),
        ("price_unit", 0),
    ],
)
def test_create_purchase_requisition_input_rejects_non_positive_numbers(field: str, value: object):
    payload = _valid_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        CreatePurchaseRequisitionInput.model_validate(payload)
