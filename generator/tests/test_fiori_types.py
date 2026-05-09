from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from erp_trace_executor.fiori_types import FioriDate


class DateModel(BaseModel):
    value: FioriDate


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
