"""High-level trace generation orchestration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from random import Random

from erp_trace_generator.artifacts import write_artifacts
from erp_trace_generator.config import load_generation_config
from erp_trace_generator.models import GeneratedArtifacts
from erp_trace_generator.planning import align_planned_step_times_to_waves, plan_cases, plan_steps, plan_waves
from erp_trace_generator.tool_validation import validate_planned_step_tool_inputs


def generate_trace_artifacts(
    *,
    config_path: str | Path,
    out_dir: str | Path,
    run_id: str,
    seed: int | None = None,
) -> GeneratedArtifacts:
    config = load_generation_config(config_path)
    effective_seed = seed if seed is not None else config.run_settings.scheduler_seed
    rng = Random(effective_seed)
    cases = plan_cases(config, rng)
    planned_steps = plan_steps(config, cases, rng)
    validate_planned_step_tool_inputs(planned_steps)
    waves = plan_waves(config, planned_steps)
    align_planned_step_times_to_waves(planned_steps, waves)
    return write_artifacts(
        config=config,
        cases=cases,
        planned_steps=planned_steps,
        waves=waves,
        out_dir=out_dir,
        run_id=run_id,
        seed=effective_seed,
        config_hash=_hash(config.raw),
        tool_catalog_hash=_hash(config.raw.get("toolRequirements", {})),
    )


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
