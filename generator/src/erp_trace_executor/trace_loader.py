"""Read JSONL traces into validated records."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from erp_trace_executor.errors import TraceParseError
from erp_trace_executor.models import TraceDefinition, TraceInitRecord, TraceRecord


def load_trace_records(path: str | Path) -> TraceDefinition:
    """Load a JSONL trace file in file order."""

    trace_path = Path(path)
    init: TraceInitRecord | None = None
    tasks: list[TraceRecord] = []

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
        if payload.get("kind") == "init":
            if init is not None:
                raise TraceParseError("Only one init record is allowed")
            if tasks:
                raise TraceParseError("Init record must be the first trace record")

            try:
                init = TraceInitRecord.model_validate(payload)
            except ValidationError as exc:
                raise TraceParseError(f"Invalid trace record on line {line_number}: {exc}") from exc

            _validate_init_users(init)
            continue

        try:
            tasks.append(TraceRecord.model_validate(payload))
        except ValidationError as exc:
            raise TraceParseError(f"Invalid trace record on line {line_number}: {exc}") from exc

    return TraceDefinition(init=init, tasks=tasks)


def _validate_init_users(init: TraceInitRecord) -> None:
    seen_sessions: set[str] = set()
    seen_users: set[str] = set()

    for user in init.users:
        if user.session_id in seen_sessions:
            raise TraceParseError(f"Duplicate init session_id '{user.session_id}'")
        seen_sessions.add(user.session_id)

        if user.user_id in seen_users:
            raise TraceParseError(f"Duplicate init user_id '{user.user_id}'")
        seen_users.add(user.user_id)
