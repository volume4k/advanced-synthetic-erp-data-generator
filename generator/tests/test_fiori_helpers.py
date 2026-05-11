from __future__ import annotations

from erp_trace_executor.tools.fiori.helpers import format_number


def test_format_number_uses_integer_string_for_whole_numbers():
    assert format_number(30.0) == "30"
    assert format_number(30.0000000001) == "30"


def test_format_number_keeps_fractional_values():
    assert format_number(1976.5) == "1976.5"
