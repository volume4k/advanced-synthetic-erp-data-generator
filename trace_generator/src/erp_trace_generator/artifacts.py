"""Write trace-generator output artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from erp_trace_generator.env import read_env_values
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import CasePlan, GenerationConfig, GeneratedArtifacts, PlannedNode


def write_artifacts(
    *,
    config: GenerationConfig,
    cases: list[CasePlan],
    nodes: list[PlannedNode],
    waves: list[dict],
    env_path: str | Path,
    out_dir: str | Path,
    run_id: str,
    seed: int,
    config_hash: str,
    tool_catalog_hash: str,
) -> GeneratedArtifacts:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    execution_trace_path = output_dir / f"{run_id}.execution-trace.yaml"
    executor_trace_path = output_dir / f"{run_id}.executor.trace.jsonl"
    post_processing_manifest_path = output_dir / f"{run_id}.post-processing-manifest.yaml"

    execution_trace = _execution_trace(config, cases, nodes, waves, run_id, seed, config_hash, tool_catalog_hash)
    manifest = _post_processing_manifest(config, cases, nodes, run_id, config_hash)

    execution_trace_path.write_text(yaml.safe_dump(execution_trace, sort_keys=False), encoding="utf-8")
    executor_trace_path.write_text(_executor_jsonl(config, nodes, waves, env_path), encoding="utf-8")
    post_processing_manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return GeneratedArtifacts(
        execution_trace_path=execution_trace_path,
        executor_trace_path=executor_trace_path,
        post_processing_manifest_path=post_processing_manifest_path,
    )


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
                "chain": [
                    "purchase_requisition",
                    "purchase_order",
                    "material_document",
                    "supplier_invoice",
                    "payment_document",
                ],
            }
            for case in cases
        ],
        "failed_case_policy": {
            "exclude_failed_cases": True,
            "source_artifacts": ["execution_log", "object_registry"],
        },
    }


def _executor_jsonl(config: GenerationConfig, nodes: list[PlannedNode], waves: list[dict], env_path: str | Path) -> str:
    env_values = read_env_values(env_path)
    nodes_by_id = {node.node_id: node for node in nodes}
    scheduled_nodes = [
        nodes_by_id[item["node_id"]]
        for wave in waves
        for item in sorted(wave["nodes"], key=lambda value: value["startup_order"])
    ]
    init_users = []
    seen_actor_ids: set[str] = set()
    actors_by_id = {actor.id: actor for actor in config.actors}

    for node in scheduled_nodes:
        if node.virtual_actor_id in seen_actor_ids:
            continue
        seen_actor_ids.add(node.virtual_actor_id)
        actor = actors_by_id[node.virtual_actor_id]
        technical_user = config.technical_user_for_actor(actor.id)
        username = env_values.get(technical_user.username_env_var)
        if username is None:
            raise TraceGenerationError(
                f"Missing username env var '{technical_user.username_env_var}' for actor '{actor.id}'"
            )
        init_user = {
            "session_id": f"{actor.id}-session",
            "user_id": actor.id,
            "username": username,
        }
        login_url = env_values.get(technical_user.login_url_env_var) or env_values.get(config.sap_login_url_env_var)
        if login_url:
            init_user["login_url"] = login_url
        init_users.append(init_user)

    records = [{"kind": "init", "users": init_users}]
    for node in scheduled_nodes:
        records.append(
            {
                "task_id": node.node_id,
                "session_id": node.session_id,
                "user_id": node.virtual_actor_id,
                "tool": node.tool_name,
                "meta": {
                    "case_id": node.case_id,
                    "node_id": node.node_id,
                    "step_type": node.step_type,
                    "virtual_actor_id": node.virtual_actor_id,
                    "technical_user_id": node.technical_user_id,
                    "target_synthetic_start": node.target_start.isoformat(),
                    "target_synthetic_end": node.target_end.isoformat(),
                },
                "input": node.inputs,
            }
        )
    return "\n".join(json.dumps(record, separators=(",", ":")) for record in records) + "\n"


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
    return next(step.step_id for step in process.steps if step.step_type == step_type)
