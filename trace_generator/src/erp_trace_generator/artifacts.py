"""Write trace-generator output artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from erp_trace_generator.artifact_models import ExecutionTraceArtifact, PostProcessingManifestArtifact
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import CasePlan, GenerationConfig, GeneratedArtifacts, PlannedStep
from erp_trace_generator.realism import CompiledRealismCriteria


def write_artifacts(
    *,
    config: GenerationConfig,
    cases: list[CasePlan],
    planned_steps: list[PlannedStep],
    waves: list[dict],
    out_dir: str | Path,
    run_id: str,
    seed: int,
    config_hash: str,
    tool_catalog_hash: str,
    realism_criteria: CompiledRealismCriteria,
) -> GeneratedArtifacts:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_trace_path = output_dir / f"{run_id}.execution-trace.yaml"
    post_processing_manifest_path = output_dir / f"{run_id}.post-processing-manifest.yaml"

    execution_trace = _validated_execution_trace(
        _execution_trace(config, cases, planned_steps, waves, run_id, seed, config_hash, tool_catalog_hash, realism_criteria)
    )
    manifest = _validated_manifest(_post_processing_manifest(config, cases, planned_steps, run_id, config_hash, realism_criteria))

    execution_trace_path.write_text(yaml.safe_dump(execution_trace, sort_keys=False), encoding="utf-8")
    post_processing_manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return GeneratedArtifacts(
        execution_trace_path=execution_trace_path,
        post_processing_manifest_path=post_processing_manifest_path,
    )


def _validated_execution_trace(payload: dict[str, Any]) -> dict[str, Any]:
    return ExecutionTraceArtifact.model_validate(payload).model_dump(mode="json", by_alias=True, exclude_none=True)


def _validated_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    validated = PostProcessingManifestArtifact.model_validate(payload).model_dump(
        mode="json",
        by_alias=True,
    )
    _validate_manifest_links(validated)
    return validated


def _validate_manifest_links(payload: dict[str, Any]) -> None:
    planned_step_ids = {item["planned_step_id"] for item in payload["planned_step_timestamps"]}
    case_ids = {item["case_id"] for item in payload["case_scenario_types"]}
    for item in payload["required_sap_object_keys"]:
        if item["planned_step_id"] not in planned_step_ids:
            raise TraceGenerationError(
                f"Manifest required SAP object keys reference unknown planned step '{item['planned_step_id']}'"
            )
        if item["case_id"] not in case_ids:
            raise TraceGenerationError(f"Manifest required SAP object keys reference unknown case '{item['case_id']}'")
    for item in payload["planned_date_input_overrides"]:
        if item["planned_step_id"] not in planned_step_ids:
            raise TraceGenerationError(
                f"Manifest planned date input override references unknown planned step '{item['planned_step_id']}'"
            )
        if item["case_id"] not in case_ids:
            raise TraceGenerationError(f"Manifest planned date input override references unknown case '{item['case_id']}'")


def _execution_trace(
    config: GenerationConfig,
    cases: list[CasePlan],
    planned_steps: list[PlannedStep],
    waves: list[dict],
    run_id: str,
    seed: int,
    config_hash: str,
    tool_catalog_hash: str,
    realism_criteria: CompiledRealismCriteria,
) -> dict[str, Any]:
    return {
        "trace_version": "0.3",
        "run_id": run_id,
        "config_hash": config_hash,
        "tool_catalog_hash": tool_catalog_hash,
        "trace_generator_version": "0.1.0",
        "realism_criteria_hash": realism_criteria.criteria_hash,
        "llm_metadata": {**realism_criteria.llm_metadata, "seed": seed},
        "actor_sessions": _session_records(config, planned_steps, realism_criteria),
        "cases": [_case_record(case) for case in cases],
        "dependency_graph": {
            "planned_steps": [_planned_step_record(planned_step) for planned_step in planned_steps],
            "dependencies": [
                {
                    "from_planned_step_id": f"{case.case_id}_{_step_id(process, dep.from_step_type)}",
                    "to_planned_step_id": f"{case.case_id}_{_step_id(process, dep.to_step_type)}",
                    "type": "data_dependency",
                    "reason": dep.description,
                }
                for case in cases
                for process in [config.process_for_scenario(case.case_scenario_type)]
                for dep in process.dependencies
            ],
        },
        "execution_schedule": {
            "mode": "waves",
            "max_parallel_actor_sessions": config.run_settings.max_parallel_actor_sessions,
            "waves": waves,
        },
        "validation_report": {"errors": [], "warnings": []},
    }


def _post_processing_manifest(
    config: GenerationConfig,
    cases: list[CasePlan],
    planned_steps: list[PlannedStep],
    run_id: str,
    config_hash: str,
    realism_criteria: CompiledRealismCriteria | None = None,
) -> dict[str, Any]:
    actor_projection = []
    actors_by_id = {actor.id: actor for actor in config.actors}
    for session in _session_records(config, planned_steps, realism_criteria):
        actor = actors_by_id[session["synthetic_actor_id"]]
        actor_projection.append(
            {
                "synthetic_actor_id": session["synthetic_actor_id"],
                "technical_sap_user_id": session["technical_sap_user_id"],
                "actor_session_id": session["actor_session_id"],
                "expose_as": actor.expose_as,
            }
        )

    manifest = {
        "manifest_version": "0.2",
        "run_id": run_id,
        "config_hash": config_hash,
        "realism_criteria_hash": realism_criteria.criteria_hash if realism_criteria is not None else None,
        "timestamp_policy": {
            "source": "planned_synthetic_time",
            "preserve_process_order": True,
            "generator_real_time_is_not_synthetic_time": True,
        },
        "actor_projection": actor_projection,
        "case_scenario_types": [
            {"case_id": case.case_id, "case_scenario_type": case.case_scenario_type}
            for case in cases
        ],
        "planned_step_timestamps": [
            {
                "planned_step_id": node.planned_step_id,
                "case_id": node.case_id,
                "step_type": node.step_type,
                "planned_synthetic_start": node.target_start.isoformat(),
                "planned_synthetic_end": node.target_end.isoformat(),
                "planned_date_inputs": node.planned_date_inputs,
            }
            for node in planned_steps
        ],
        "required_sap_object_keys": [
            {
                "planned_step_id": node.planned_step_id,
                "case_id": node.case_id,
                "required_sap_object_keys": node.required_sap_object_keys,
            }
            for node in planned_steps
        ],
        "object_lineage": [
            {
                "case_id": case.case_id,
                "chain": _object_lineage_chain(config, case.process_type, case.case_scenario_type),
            }
            for case in cases
        ],
        "post_processing_exports": [
            {"id": export.id, "description": export.description}
            for export in config.run_settings.post_processing_export_groups
        ],
        "planned_date_input_overrides": _planned_date_input_overrides(planned_steps),
        "failed_process_case_policy": {
            "exclude_failed_cases": True,
            "source_artifacts": ["execution_log", "object_registry"],
        },
    }
    return manifest


def _case_record(case: CasePlan) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "process_type": case.process_type,
        "case_scenario_type": case.case_scenario_type,
        "requested_delivery_date": (
            case.requested_delivery_date.isoformat()
            if case.requested_delivery_date is not None
            else None
        ),
        "line_items": [
            {
                "line_id": f"{case.case_id}_L1",
                "material_id": case.material_id,
                "vendor_id": case.vendor_id,
                "plant": case.plant,
                "purchasing_org": case.purchasing_org,
                "storage_location": case.storage_location,
                "quantity": case.quantity,
                "target_price": case.target_price,
            }
        ],
    }


def _planned_date_input_overrides(planned_steps: list[PlannedStep]) -> list[dict[str, str]]:
    overrides: list[dict[str, str]] = []
    for node in planned_steps:
        for override in node.runtime_date_overrides:
            for field in override.fields:
                planned_value = node.planned_date_inputs.get(field)
                if planned_value is None:
                    raise ValueError(
                        f"Planned step '{node.planned_step_id}' field '{field}' in node.runtime_date_overrides "
                        "has no matching value in node.planned_date_inputs; planned_value=None"
                    )
                overrides.append(
                    {
                        "planned_step_id": node.planned_step_id,
                        "case_id": node.case_id,
                        "step_type": node.step_type,
                        "object_type": override.object_type,
                        "field": field,
                        "planned_value": planned_value,
                        "runtime_value_policy": override.runtime_value_policy,
                        "source": override.source,
                        "reason": override.reason,
                    }
                )
    return overrides


def _session_records(
    config: GenerationConfig,
    planned_steps: list[PlannedStep],
    realism_criteria: CompiledRealismCriteria | None = None,
) -> list[dict[str, Any]]:
    actor_ids_by_session: dict[str, str] = {}
    source_steps_by_session: dict[str, str] = {}
    for node in sorted(planned_steps, key=lambda item: item.planned_step_id):
        actor_id = actor_ids_by_session.get(node.actor_session_id)
        if actor_id is not None and actor_id != node.synthetic_actor_id:
            raise TraceGenerationError(
                f"Actor session '{node.actor_session_id}' is used by actors '{actor_id}' and "
                f"'{node.synthetic_actor_id}' on planned steps '{source_steps_by_session[node.actor_session_id]}' and "
                f"'{node.planned_step_id}'"
            )
        actor_ids_by_session[node.actor_session_id] = node.synthetic_actor_id
        source_steps_by_session.setdefault(node.actor_session_id, node.planned_step_id)
    records: list[dict[str, Any]] = []
    for actor_session_id, actor_id in actor_ids_by_session.items():
        technical_user = config.technical_user_for_actor(actor_id)
        records.append(
            {
                "actor_session_id": actor_session_id,
                "synthetic_actor_id": actor_id,
                "technical_sap_user_id": technical_user.id,
                "username_env_var": technical_user.username_env_var,
                "password_env_var": technical_user.password_env_var,
                "login_url_env_var": technical_user.login_url_env_var,
                **_human_delay_profile(actor_id, realism_criteria),
            }
        )
    return records


def _human_delay_profile(actor_id: str, realism_criteria: CompiledRealismCriteria | None) -> dict[str, Any]:
    if realism_criteria is None:
        return {}
    criteria = realism_criteria.actor_criteria.get(actor_id)
    if criteria is None:
        return {}
    return {
        "human_delay_profile": {
            "delay_multiplier": criteria.delay_multiplier,
        }
    }


def _planned_step_record(node: PlannedStep) -> dict[str, Any]:
    return {
        "planned_step_id": node.planned_step_id,
        "case_id": node.case_id,
        "step_type": node.step_type,
        "tool_name": node.tool_name,
        "synthetic_actor_id": node.synthetic_actor_id,
        "technical_sap_user_id": node.technical_sap_user_id,
        "actor_session_id": node.actor_session_id,
        "inputs": node.inputs,
        "required_sap_object_keys": node.required_sap_object_keys,
        "planned_date_inputs": node.planned_date_inputs,
        "planned_synthetic_time": {
            "start": node.target_start.isoformat(),
            "end": node.target_end.isoformat(),
        },
        "labels": node.labels,
    }


def _step_id(process, step_type: str) -> str:
    step_id = next((step.step_id for step in process.steps if step.step_type == step_type), None)
    if step_id is None:
        raise TraceGenerationError(f"Process '{process.process_type}' has no step type '{step_type}'")
    return step_id


def _object_lineage_chain(config: GenerationConfig, process_type: str, scenario_type: str) -> list[str]:
    process = next(
        (item for item in config.processes if item.process_type == process_type and item.scenario_type == scenario_type),
        None,
    )
    if process is None:
        raise TraceGenerationError(
            f"Cannot build object lineage for unknown process type '{process_type}' and scenario '{scenario_type}'"
        )

    chain: list[str] = []
    for step in process.steps:
        for output in step.required_sap_object_keys:
            object_type = output.split(".", maxsplit=1)[0]
            if object_type and object_type not in chain:
                chain.append(object_type)
    if not chain:
        raise TraceGenerationError(f"Process '{process_type}' has no required SAP object keys for object lineage")
    return chain
