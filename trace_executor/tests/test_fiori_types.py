from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from erp_trace_executor.fiori_types import FioriCurrency, FioriDate, runtime_safe_fiori_date


class DateModel(BaseModel):
    value: FioriDate


class CurrencyModel(BaseModel):
    value: FioriCurrency


def test_fiori_date_accepts_exact_mm_dd_yyyy_string():
    model = DateModel.model_validate({"value": "05/09/2026"})

    assert model.value == "05/09/2026"


@pytest.mark.parametrize(
    "value",
    [
        "5/09/2026",
        "05/9/2026",
        "13/01/2026",
        "00/01/2026",
        "01/00/2026",
        "01/32/2026",
        "01/01/1999",
        "01/01/2051",
        "02/31/2025",
    ],
)
def test_fiori_date_rejects_invalid_dates(value: str):
    with pytest.raises(ValidationError):
        DateModel.model_validate({"value": value})


def test_fiori_date_rejects_non_string_values():
    with pytest.raises(ValidationError):
        DateModel.model_validate({"value": 20260509})


def test_runtime_safe_fiori_date_keeps_past_or_current_date():
    assert runtime_safe_fiori_date("05/19/2026", today=date(2026, 5, 20)) == "05/19/2026"
    assert runtime_safe_fiori_date("05/20/2026", today=date(2026, 5, 20)) == "05/20/2026"


def test_runtime_safe_fiori_date_clips_future_date_to_today():
    assert runtime_safe_fiori_date("06/01/2026", today=date(2026, 5, 20)) == "05/20/2026"


def test_fiori_currency_accepts_three_uppercase_letters():
    model = CurrencyModel.model_validate({"value": "USD"})

    assert model.value == "USD"


@pytest.mark.parametrize("value", ["usd", "US", "US01", 123])
def test_fiori_currency_rejects_invalid_values(value: object):
    with pytest.raises(ValidationError):
        CurrencyModel.model_validate({"value": value})
