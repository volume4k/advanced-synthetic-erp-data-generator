"""Append-only execution evidence artifacts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from erp_trace_executor.errors import TraceExecutorError

LOGGER = logging.getLogger(__name__)
UNSAFE_RUN_ID_CHARS = {"/", "\\", ":", "*", "?", "<", ">", "|"}
SENSITIVE_FIELD_NAMES = {"password", "password_value", "input", "inputs"}

EVENT_SEVERITY_BY_TYPE = {
    "state_updated": "DEBUG",
    "planned_step_skipped": "WARNING",
    "login_interrupted": "WARNING",
    "planned_step_interrupted": "WARNING",
    "run_interrupted": "WARNING",
}


class ExecutionEvidenceWriter:
    def __init__(self, artifact_dir: str | Path, *, run_id: str) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.run_id = _safe_run_id(run_id)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_root = self.artifact_dir.resolve()
        self.execution_log_path = _artifact_path(artifact_root, self.run_id, ".execution-log.jsonl")
        self.object_registry_path = _artifact_path(artifact_root, self.run_id, ".object-registry.jsonl")

    def log_event(self, event_type: str, **fields: Any) -> None:
        clean_fields = _clean(fields)
        severity = _severity_for(event_type)
        message = _message_for(event_type, clean_fields)
        self._append(
            self.execution_log_path,
            {
                "event_type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "severity": severity,
                "message": message,
                **clean_fields,
            },
        )
        LOGGER.log(getattr(logging, severity), message)

    def record_object(self, **fields: Any) -> None:
        self._append(
            self.object_registry_path,
            {
                "run_id": self.run_id,
                **_clean(fields),
            },
        )

    def _append(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
                handle.flush()
        except OSError as exc:
            LOGGER.exception(
                "Failed to write execution evidence to %s; payload keys=%s count=%s",
                path,
                sorted(payload),
                len(payload),
            )
            raise TraceExecutorError(f"Failed to write execution evidence to '{path}': {exc}") from exc


def _safe_run_id(run_id: str) -> str:
    if not run_id:
        raise ValueError("run_id must not be empty")
    path = Path(run_id)
    if (
        path.is_absolute()
        or path.name != run_id
        or run_id in {".", ".."}
        or any(char in run_id for char in UNSAFE_RUN_ID_CHARS)
    ):
        raise ValueError(f"run_id contains unsafe filename characters: {run_id!r}")
    return run_id


def _artifact_path(artifact_root: Path, run_id: str, suffix: str) -> Path:
    path = (artifact_root / f"{run_id}{suffix}").resolve()
    if not path.is_relative_to(artifact_root):
        raise ValueError(f"evidence artifact path escapes artifact directory: {path}")
    return path


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in fields.items()
        if value is not None and key.lower() not in SENSITIVE_FIELD_NAMES
    }


def _severity_for(event_type: str) -> str:
    if event_type in EVENT_SEVERITY_BY_TYPE:
        return EVENT_SEVERITY_BY_TYPE[event_type]
    if event_type.endswith("_failed"):
        return "ERROR"
    return "INFO"


def _message_for(event_type: str, fields: dict[str, Any]) -> str:
    planned_step_id = fields.get("planned_step_id")
    actor_session_id = fields.get("actor_session_id")
    wave_id = fields.get("wave_id")
    error = _compact_text(fields.get("error"))

    if event_type == "run_started":
        return "Executor run started"
    if event_type == "run_completed":
        failed_case_count = fields.get("failed_case_count")
        return f"Executor run completed with {failed_case_count} failed cases"
    if event_type == "run_failed":
        return _join_message("Executor run failed", error)
    if event_type == "run_interrupted":
        return _join_message("Executor run interrupted by user", _planned_step_suffix(planned_step_id))
    if event_type == "login_started":
        return f"Login started for actor session {actor_session_id}"
    if event_type == "login_succeeded":
        return f"Login succeeded for actor session {actor_session_id}"
    if event_type == "login_failed":
        return _join_message(f"Login failed for actor session {actor_session_id}", error)
    if event_type == "login_interrupted":
        return f"Login interrupted by user for actor session {actor_session_id}"
    if event_type == "wave_started":
        return f"Execution wave {wave_id} started"
    if event_type == "wave_completed":
        return f"Execution wave {wave_id} completed"
    if event_type == "planned_step_started":
        return f"Planned step {planned_step_id} started"
    if event_type == "planned_step_succeeded":
        return f"Planned step {planned_step_id} succeeded"
    if event_type == "planned_step_skipped":
        reason = fields.get("reason")
        if reason:
            return f"Skipped planned step {planned_step_id}: {reason}"
        return f"Skipped planned step {planned_step_id}"
    if event_type == "planned_step_failed":
        return _join_message(f"Failed planned step {planned_step_id}", error, _sap_message_suffix(fields))
    if event_type == "planned_step_interrupted":
        return f"Interrupted planned step {planned_step_id} by user"
    if event_type == "case_failed":
        return _join_message(f"Failed process case {fields.get('case_id')}", error, _sap_message_suffix(fields))
    if event_type == "home_reset_failed":
        return _join_message(f"Home reset failed for planned step {planned_step_id}", error)
    if event_type == "state_updated":
        return f"State updated for planned step {planned_step_id}"
    return event_type.replace("_", " ")


def _join_message(prefix: str, *parts: str) -> str:
    suffixes = [part for part in parts if part]
    if not suffixes:
        return prefix
    return f"{prefix}: {'; '.join(suffixes)}"


def _planned_step_suffix(planned_step_id: object) -> str:
    return f"planned step {planned_step_id}" if planned_step_id else ""


def _sap_message_suffix(fields: dict[str, Any]) -> str:
    messages = fields.get("sap_messages")
    if not isinstance(messages, list):
        return ""
    texts = [
        _compact_text(message.get("text"))
        for message in messages
        if isinstance(message, dict) and message.get("text")
    ]
    if not texts:
        return ""
    return f"SAP messages: {', '.join(texts)}"


def _compact_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())[:2_000]
