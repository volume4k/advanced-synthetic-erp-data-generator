from __future__ import annotations

import pytest

from erp_trace_executor.tools.fiori.send_payment import (
    _extract_payment_document,
    _format_amount,
)


def test_extract_payment_document_from_success_message():
    message = "Buchungsbeleg 1500000004 (Kreditorenzahlung) wurde gebucht"

    assert _extract_payment_document(message) == "1500000004"


def test_extract_payment_document_rejects_missing_success_document():
    with pytest.raises(ValueError, match="Could not extract payment document"):
        _extract_payment_document("Keine Zahlung gebucht")


def test_format_amount_uses_two_decimals():
    assert _format_amount(200) == "200.00"
    assert _format_amount(1976.5) == "1976.50"
