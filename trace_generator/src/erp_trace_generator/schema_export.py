"""Export JSON Schemas for trace-generator artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from erp_trace_generator.artifact_models import ExecutionTraceArtifact, PostProcessingManifestArtifact

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def schema_output_paths() -> tuple[Path, Path]:
    return (
        SCHEMA_DIR / "execution-trace.schema.json",
        SCHEMA_DIR / "post-processing-manifest.schema.json",
    )


def write_schema_files() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    execution_schema_path, manifest_schema_path = schema_output_paths()
    execution_schema_path.write_text(
        json.dumps(ExecutionTraceArtifact.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_schema_path.write_text(
        json.dumps(PostProcessingManifestArtifact.model_json_schema(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_schema_files()
