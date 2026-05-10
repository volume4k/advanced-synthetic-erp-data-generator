from __future__ import annotations

import pytest

from erp_trace_executor.tools.fiori.send_payment import (
    _accounting_document_pattern,
    _extract_payment_document,
    _format_amount,
)


def test_extract_payment_document_from_success_message():
    message = "Buchungsbeleg 1500000004 (Kreditorenzahlung) wurde gebucht"

    assert _extract_payment_document(message) == "1500000004"


def test_extract_payment_document_rejects_missing_success_document():
    with pytest.raises(ValueError, match="Could not extract payment document"):
        _extract_payment_document("Keine Zahlung gebucht")


@pytest.mark.parametrize(
    "text",
    [
        "5105600103",
        "Buchungsbeleg 5105600103",
        "Buchungsbeleg 5105600103 anzeigen",
    ],
)
def test_accounting_document_pattern_matches_visible_link_text(text: str):
    assert _accounting_document_pattern("5105600103").search(text)


def test_format_amount_uses_two_decimals():
    assert _format_amount(200) == "200.00"
    assert _format_amount(1976.5) == "1976.50"
