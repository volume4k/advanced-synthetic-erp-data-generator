"""Read JSONL traces into validated records."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.models import TraceRecord


def load_trace_records(path: str | Path) -> list[TraceRecord]:
    """Load a JSONL trace file in file order."""

    trace_path = Path(path)
    records: list[TraceRecord] = []

    for line_number, raw_line in enumerate(trace_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraceParseError(f"Invalid JSON on line {line_number}: {exc.msg}") from exc

        if not isinstance(payload, dict):
            raise TraceParseError(f"Invalid trace record on line {line_number}: expected a JSON object")

        payload["line_number"] = line_number

        try:
            records.append(TraceRecord.model_validate(payload))
        except ValidationError as exc:
            raise TraceParseError(f"Invalid trace record on line {line_number}: {exc}") from exc

    return records
