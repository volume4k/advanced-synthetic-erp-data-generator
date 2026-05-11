"""CLI entrypoint for the trace executor."""

from __future__ import annotations

import argparse
from builtins import input as console_input
import json
import sys
import traceback
from pathlib import Path

from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import load_env_credentials
from erp_trace_executor.executor import TraceExecutor
from erp_trace_executor.trace_loader import load_trace_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute a JSONL ERP browser trace.")
    parser.add_argument("trace_path", type=Path, help="Path to the JSONL trace file")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path("configuration/.env"),
        help="Path to .env credentials file. Defaults to configuration/.env",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch Chromium in headed mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session_manager: BrowserSessionManager | None = None

    try:
        records = load_trace_records(args.trace_path)
        credential_store = load_env_credentials(args.env_file)
        executor = TraceExecutor(credential_store=credential_store)
        session_manager = BrowserSessionManager(headless=not args.headed)
        results = executor.execute(
            records,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        if session_manager is not None and args.headed:
            print(
                "Execution failed. Browser remains open for manual SAP cleanup.",
                file=sys.stderr,
            )
            try:
                console_input("Press Enter after cleanup to close browser and exit...")
            except (EOFError, KeyboardInterrupt):
                pass
        return 1
    finally:
        if session_manager is not None:
            session_manager.close()

    print(json.dumps([result.to_dict() for result in results], indent=2))
    return 0
