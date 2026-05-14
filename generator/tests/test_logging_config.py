from __future__ import annotations

import pytest

from erp_trace_executor.logging_config import configure_logging


def test_configure_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="Unsupported log level 'VERBOSE'"):
        configure_logging("VERBOSE")
