"""CLI entrypoint for the trace executor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.errors import TraceExecutorError
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.trace_loader import load_trace_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute a JSONL ERP browser trace.")
    parser.add_argument("trace_path", type=Path, help="Path to the JSONL trace file")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch Chromium in headed mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        records = load_trace_records(args.trace_path)
        executor = TraceExecutor()
        with BrowserSessionManager(headless=not args.headed) as session_manager:
            results = executor.execute(
                records,
                context_factory=lambda record: ExecutionContext(
                    record=record,
                    session_manager=session_manager,
                ),
            )
    except TraceExecutorError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps([result.to_dict() for result in results], indent=2))
    return 0
