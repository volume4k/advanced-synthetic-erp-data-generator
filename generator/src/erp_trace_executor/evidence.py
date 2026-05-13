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


class ExecutionEvidenceWriter:
    def __init__(self, artifact_dir: str | Path, *, run_id: str) -> None:
        self.artifact_dir = Path(artifact_dir)
        self.run_id = _safe_run_id(run_id)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_root = self.artifact_dir.resolve()
        self.execution_log_path = _artifact_path(artifact_root, self.run_id, ".execution-log.jsonl")
        self.object_registry_path = _artifact_path(artifact_root, self.run_id, ".object-registry.jsonl")

    def log_event(self, event_type: str, **fields: Any) -> None:
        self._append(
            self.execution_log_path,
            {
                "event_type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                **_clean(fields),
            },
        )

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
    return {key: value for key, value in fields.items() if value is not None}
