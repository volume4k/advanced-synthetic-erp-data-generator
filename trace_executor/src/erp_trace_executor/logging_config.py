"""Logging configuration for executor CLI runs."""

from __future__ import annotations

import logging
import sys

LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOGGER_NAME = "erp_trace_executor"
_HANDLER_MARKER = "_erp_trace_executor_cli_handler"


def configure_logging(level_name: str) -> None:
    """Configure package logging for CLI execution."""

    normalized_level = level_name.upper()
    if normalized_level not in LOG_LEVEL_NAMES:
        raise ValueError(f"Unsupported log level {level_name!r}")
    level = getattr(logging, normalized_level)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
