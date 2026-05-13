"""Write trace-generator output artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from erp_trace_generator.artifact_models import ExecutionTraceArtifact, PostProcessingManifestArtifact
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import CasePlan, GenerationConfig, GeneratedArtifacts, PlannedNode


def write_artifacts(
    *,
    config: GenerationConfig,
    cases: list[CasePlan],
    nodes: list[PlannedNode],
    waves: list[dict],
    out_dir: str | Path,
    run_id: str,
    seed: int,
    config_hash: str,
    tool_catalog_hash: str,
) -> GeneratedArtifacts:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_trace_path = output_dir / f"{run_id}.execution-trace.yaml"
    post_processing_manifest_path = output_dir / f"{run_id}.post-processing-manifest.yaml"

    execution_trace = _validated_execution_trace(
        _execution_trace(config, cases, nodes, waves, run_id, seed, config_hash, tool_catalog_hash)
    )
    manifest = _validated_manifest(_post_processing_manifest(config, cases, nodes, run_id, config_hash))

    execution_trace_path.write_text(yaml.safe_dump(execution_trace, sort_keys=False), encoding="utf-8")
    post_processing_manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return GeneratedArtifacts(
        execution_trace_path=execution_trace_path,
        post_processing_manifest_path=post_processing_manifest_path,
    )


def _validated_execution_trace(payload: dict[str, Any]) -> dict[str, Any]:
    return ExecutionTraceArtifact.model_validate(payload).model_dump(mode="json", by_alias=True)


def _validated_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    validated = PostProcessingManifestArtifact.model_validate(payload).model_dump(mode="json", by_alias=True)
    _validate_manifest_links(validated)
    return validated


def _validate_manifest_links(payload: dict[str, Any]) -> None:
    node_ids = {item["node_id"] for item in payload["node_timestamps"]}
    case_ids = {item["case_id"] for item in payload["case_labels"]}
    for item in payload["expected_object_keys"]:
        if item["node_id"] not in node_ids:
            raise TraceGenerationError(f"Manifest expected object keys reference unknown node '{item['node_id']}'")
        if item["case_id"] not in case_ids:
            raise TraceGenerationError(f"Manifest expected object keys reference unknown case '{item['case_id']}'")


def _execution_trace(
    config: GenerationConfig,
    cases: list[CasePlan],
    nodes: list[PlannedNode],
    waves: list[dict],
    run_id: str,
    seed: int,
    config_hash: str,
    tool_catalog_hash: str,
) -> dict[str, Any]:
    process = config.active_process()
    return {
        "trace_version": "0.1",
        "run_id": run_id,
        "config_hash": config_hash,
        "tool_catalog_hash": tool_catalog_hash,
        "trace_generator_version": "0.1.0",
        "llm_metadata": {"used": False, "seed": seed},
        "sessions": _session_records(config, nodes),
        "cases": [_case_record(case) for case in cases],
        "dependency_graph": {
            "nodes": [_node_record(node) for node in nodes],
            "edges": [
                {"from": f"{case.case_id}_{_step_id(process, dep.from_step_type)}", "to": f"{case.case_id}_{_step_id(process, dep.to_step_type)}", "type": "data_dependency", "reason": dep.description}
                for case in cases
                for dep in process.dependencies
            ],
        },
        "execution_schedule": {
            "mode": "waves",
            "max_parallel_sessions": config.run_settings.max_parallel_sessions,
            "waves": waves,
        },
        "validation_report": {"errors": [], "warnings": []},
    }


def _post_processing_manifest(
    config: GenerationConfig,
    cases: list[CasePlan],
    nodes: list[PlannedNode],
    run_id: str,
    config_hash: str,
) -> dict[str, Any]:
    actor_projection = []
    for actor in config.actors:
        technical_user = config.technical_user_for_actor(actor.id)
        actor_projection.append(
            {
                "virtual_actor_id": actor.id,
                "technical_user_id": technical_user.id,
                "session_id": f"{actor.id}-session",
                "expose_as": actor.expose_as,
            }
        )

    return {
        "manifest_version": "0.1",
        "run_id": run_id,
        "config_hash": config_hash,
        "timestamp_policy": {
            "source": "planned_target_synthetic_time",
            "preserve_process_order": True,
            "generator_real_time_is_not_synthetic_time": True,
        },
        "actor_projection": actor_projection,
        "case_labels": [
            {"case_id": case.case_id, "scenario_id": case.scenario_id, "case_label": case.case_label}
            for case in cases
        ],
        "node_timestamps": [
            {
                "node_id": node.node_id,
                "case_id": node.case_id,
                "step_type": node.step_type,
                "target_synthetic_start": node.target_start.isoformat(),
                "target_synthetic_end": node.target_end.isoformat(),
                "business_dates": node.business_dates,
            }
            for node in nodes
        ],
        "expected_object_keys": [
            {
                "node_id": node.node_id,
                "case_id": node.case_id,
                "expected_outputs": node.expected_outputs,
            }
            for node in nodes
        ],
        "object_lineage": [
            {
                "case_id": case.case_id,
                "chain": _object_lineage_chain(config, case.process_type),
            }
            for case in cases
        ],
        "post_processing_exports": [
            {"id": export.id, "description": export.description}
            for export in config.run_settings.post_processing_export_groups
        ],
        "failed_case_policy": {
            "exclude_failed_cases": True,
            "source_artifacts": ["execution_log", "object_registry"],
        },
    }


def _case_record(case: CasePlan) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "process_type": case.process_type,
        "scenario_id": case.scenario_id,
        "case_label": case.case_label,
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


def _session_records(config: GenerationConfig, nodes: list[PlannedNode]) -> list[dict[str, Any]]:
    actor_ids_by_session: dict[str, str] = {}
    source_nodes_by_session: dict[str, str] = {}
    for node in sorted(nodes, key=lambda item: item.node_id):
        actor_id = actor_ids_by_session.get(node.session_id)
        if actor_id is not None and actor_id != node.virtual_actor_id:
            raise TraceGenerationError(
                f"Session '{node.session_id}' is used by actors '{actor_id}' and "
                f"'{node.virtual_actor_id}' on nodes '{source_nodes_by_session[node.session_id]}' and '{node.node_id}'"
            )
        actor_ids_by_session[node.session_id] = node.virtual_actor_id
        source_nodes_by_session.setdefault(node.session_id, node.node_id)
    records: list[dict[str, Any]] = []
    for session_id, actor_id in actor_ids_by_session.items():
        technical_user = config.technical_user_for_actor(actor_id)
        records.append(
            {
                "session_id": session_id,
                "virtual_actor_id": actor_id,
                "technical_user_id": technical_user.id,
                "username_env_var": technical_user.username_env_var,
                "password_env_var": technical_user.password_env_var,
                "login_url_env_var": technical_user.login_url_env_var,
            }
        )
    return records


def _node_record(node: PlannedNode) -> dict[str, Any]:
    return {
        "node_id": node.node_id,
        "case_id": node.case_id,
        "step_type": node.step_type,
        "tool_name": node.tool_name,
        "virtual_actor_id": node.virtual_actor_id,
        "technical_sap_user": node.technical_user_id,
        "session_id": node.session_id,
        "inputs": node.inputs,
        "expected_outputs": node.expected_outputs,
        "business_dates": node.business_dates,
        "target_synthetic_time": {
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


def _object_lineage_chain(config: GenerationConfig, process_type: str) -> list[str]:
    process = next((item for item in config.processes if item.process_type == process_type), None)
    if process is None:
        raise TraceGenerationError(f"Cannot build object lineage for unknown process type '{process_type}'")

    chain: list[str] = []
    for step in process.steps:
        for output in step.expected_outputs:
            object_type = output.split(".", maxsplit=1)[0]
            if object_type and object_type not in chain:
                chain.append(object_type)
    if not chain:
        raise TraceGenerationError(f"Process '{process_type}' has no expected outputs for object lineage")
    return chain
