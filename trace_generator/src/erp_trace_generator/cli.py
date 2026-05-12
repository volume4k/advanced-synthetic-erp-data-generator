"""CLI entrypoint for trace generation."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
import traceback

from erp_trace_generator.generator import generate_trace_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate ERP trace artifacts from compiled configuration YAML.")
    parser.add_argument("config_path", type=Path, help="Path to compiled Pkl YAML, usually configuration/build/main.yaml")
    parser.add_argument("--env-file", type=Path, default=Path("configuration/.env"), help="Path to env file with SAP usernames and login URL")
    parser.add_argument("--out-dir", type=Path, default=Path("trace_generator/build"), help="Output directory for generated artifacts")
    parser.add_argument("--run-id", default=None, help="Run id prefix for generated artifacts")
    parser.add_argument("--seed", type=int, default=None, help="Override scheduler seed from config")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = args.run_id or f"RUN_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        artifacts = generate_trace_artifacts(
            config_path=args.config_path,
            env_path=args.env_file,
            out_dir=args.out_dir,
            run_id=run_id,
            seed=args.seed,
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1

    print(f"Wrote {artifacts.execution_trace_path}")
    print(f"Wrote {artifacts.executor_trace_path}")
    print(f"Wrote {artifacts.post_processing_manifest_path}")
    return 0
