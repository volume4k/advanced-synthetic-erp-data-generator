"""Reusable Fiori input types."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator

FIORI_DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
FIORI_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def validate_fiori_date(value: str) -> str:
    """Validate exact MM/DD/YYYY dates while keeping the runtime value as a string."""

    if not isinstance(value, str):
        raise ValueError("Fiori date must be a string")
    if FIORI_DATE_PATTERN.fullmatch(value) is None:
        raise ValueError("Fiori date must use exact MM/DD/YYYY format")

    month, day, year = (int(part) for part in value.split("/"))
    if not 1 <= month <= 12:
        raise ValueError("Fiori date month must be between 01 and 12")
    if not 1 <= day <= 31:
        raise ValueError("Fiori date day must be between 01 and 31")
    if not 2000 <= year <= 2050:
        raise ValueError("Fiori date year must be between 2000 and 2050")

    try:
        datetime.strptime(value, "%m/%d/%Y")
    except ValueError as exc:
        raise ValueError("Fiori date must be a valid calendar date") from exc

    return value


def validate_fiori_currency(value: str) -> str:
    """Validate exact three-letter uppercase currency codes."""

    if not isinstance(value, str):
        raise ValueError("Fiori currency must be a string")
    if FIORI_CURRENCY_PATTERN.fullmatch(value) is None:
        raise ValueError("Fiori currency must use exact ISO-style uppercase format")
    return value


FioriDate = Annotated[str, AfterValidator(validate_fiori_date)]
FioriCurrency = Annotated[str, AfterValidator(validate_fiori_currency)]
