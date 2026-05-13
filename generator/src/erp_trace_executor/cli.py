"""CLI entrypoint for the trace executor."""

from __future__ import annotations

import argparse
from builtins import input as console_input
import json
import sys
import traceback
from pathlib import Path

from erp_trace_executor.browser.session import BrowserSessionManager
from erp_trace_executor.canonical import build_init_from_sessions, load_canonical_trace
from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.credentials import load_env_credentials, read_env_values
from erp_trace_executor.evidence import ExecutionEvidenceWriter
from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.executor import TraceExecutor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute a canonical ERP execution trace YAML.")
    parser.add_argument("trace_path", type=Path, help="Path to the canonical execution-trace YAML file")
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
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Directory for execution evidence artifacts. Defaults to the trace file directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    session_manager: BrowserSessionManager | None = None

    try:
        if args.trace_path.suffix.lower() not in {".yaml", ".yml"}:
            raise TraceParseError(
                f"Unsupported trace format '{args.trace_path.suffix}'. "
                "Only canonical execution trace YAML files (.yaml/.yml) are supported."
            )

        credential_store = load_env_credentials(args.env_file)
        executor = TraceExecutor(credential_store=credential_store)
        session_manager = BrowserSessionManager(headless=not args.headed)
        trace = load_canonical_trace(args.trace_path)
        env_values = read_env_values(args.env_file)
        init = build_init_from_sessions(trace, env_values)
        artifact_dir = args.artifact_dir or args.trace_path.parent
        results = executor.execute_canonical(
            trace,
            init=init,
            context_factory=lambda record: ExecutionContext(
                record=record,
                session_manager=session_manager,
            ),
            evidence_writer=ExecutionEvidenceWriter(artifact_dir, run_id=trace.run_id),
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
