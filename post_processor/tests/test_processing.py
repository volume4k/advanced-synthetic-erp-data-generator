from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

from erp_sap_export.processing import _projection_stats, process_dataset


def test_process_dataset_filters_failed_case_projects_eban_and_preserves_raw(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    trace_path = tmp_path / "RUN_BA-210.execution-trace.yaml"
    manifest_path = tmp_path / "RUN_BA-210.post-processing-manifest.yaml"
    log_path = tmp_path / "RUN_BA-210.execution-log.jsonl"
    registry_path = tmp_path / "RUN_BA-210.object-registry.jsonl"

    _write_csv(
        raw_dir / "EBAN.csv",
        [
            {
                "BANFN": "0010000317",
                "BADAT": "06/01/2026",
                "BEDAT": "06/01/2026",
                "ERDAT": "06/01/2026",
                "LFDAT": "06/02/2026",
                "ERNAM": "LEARN-800",
            },
            {
                "BANFN": "0010000393",
                "BADAT": "06/01/2026",
                "BEDAT": "06/01/2026",
                "ERDAT": "06/01/2026",
                "LFDAT": "06/02/2026",
                "ERNAM": "LEARN-800",
            },
        ],
    )
    raw_before = (raw_dir / "EBAN.csv").read_text(encoding="utf-8")

    trace_path.write_text(
        yaml.safe_dump(
            {
                "run_id": "RUN_BA-210",
                "cases": [
                    {"case_id": "C005", "case_scenario_type": "NORMAL"},
                    {"case_id": "C081", "case_scenario_type": "NORMAL"},
                ],
                "dependency_graph": {
                    "planned_steps": [
                        {
                            "planned_step_id": "C005_A1",
                            "case_id": "C005",
                            "step_type": "create_purchase_requisition",
                            "tool_name": "fiori.create_purchase_requisition",
                            "synthetic_actor_id": "inventory_manager_mi00",
                            "technical_sap_user_id": "TU_01",
                            "actor_session_id": "inventory_manager_mi00-session",
                            "inputs": {},
                            "required_sap_object_keys": ["purchase_requisition.pr_number"],
                            "planned_date_inputs": {"delivery_date": "2026-06-15"},
                            "planned_synthetic_time": {
                                "start": "2026-06-01T10:39:04-04:00",
                                "end": "2026-06-01T10:50:04-04:00",
                            },
                            "labels": {},
                        },
                        {
                            "planned_step_id": "C081_A1",
                            "case_id": "C081",
                            "step_type": "create_purchase_requisition",
                            "tool_name": "fiori.create_purchase_requisition",
                            "synthetic_actor_id": "inventory_manager_mi00",
                            "technical_sap_user_id": "TU_01",
                            "actor_session_id": "inventory_manager_mi00-session",
                            "inputs": {},
                            "required_sap_object_keys": ["purchase_requisition.pr_number"],
                            "planned_date_inputs": {"delivery_date": "2026-06-22"},
                            "planned_synthetic_time": {
                                "start": "2026-06-09T08:00:00-04:00",
                                "end": "2026-06-09T08:12:00-04:00",
                            },
                            "labels": {},
                        },
                    ],
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "manifest_version": "0.2",
                "run_id": "RUN_BA-210",
                "actor_projection": [
                    {
                        "synthetic_actor_id": "inventory_manager_mi00",
                        "technical_sap_user_id": "TU_01",
                        "actor_session_id": "inventory_manager_mi00-session",
                        "expose_as": "inventory_manager_mi00_SYN",
                    }
                ],
                "planned_step_timestamps": [
                    {
                        "planned_step_id": "C005_A1",
                        "case_id": "C005",
                        "step_type": "create_purchase_requisition",
                        "planned_synthetic_start": "2026-06-01T10:39:04-04:00",
                        "planned_synthetic_end": "2026-06-01T10:50:04-04:00",
                        "planned_date_inputs": {"delivery_date": "2026-06-15"},
                    },
                    {
                        "planned_step_id": "C081_A1",
                        "case_id": "C081",
                        "step_type": "create_purchase_requisition",
                        "planned_synthetic_start": "2026-06-09T08:00:00-04:00",
                        "planned_synthetic_end": "2026-06-09T08:12:00-04:00",
                        "planned_date_inputs": {"delivery_date": "2026-06-22"},
                    },
                ],
                "failed_process_case_policy": {"exclude_failed_cases": True},
                "planned_date_input_overrides": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"event_type": "planned_step_succeeded", "case_id": "C005", "planned_step_id": "C005_A1"}),
                json.dumps({"event_type": "case_failed", "case_id": "C081", "planned_step_id": "C081_A5"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "run_id": "RUN_BA-210",
                        "case_id": "C005",
                        "planned_step_id": "C005_A1",
                        "tool": "fiori.create_purchase_requisition",
                        "synthetic_actor_id": "inventory_manager_mi00",
                        "technical_sap_user_id": "TU_01",
                        "object_type": "purchase_requisition",
                        "keys": {"pr_number": "10000317"},
                    }
                ),
                json.dumps(
                    {
                        "run_id": "RUN_BA-210",
                        "case_id": "C081",
                        "planned_step_id": "C081_A1",
                        "tool": "fiori.create_purchase_requisition",
                        "synthetic_actor_id": "inventory_manager_mi00",
                        "technical_sap_user_id": "TU_01",
                        "object_type": "purchase_requisition",
                        "keys": {"pr_number": "10000393"},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = process_dataset(
        raw_dir=raw_dir,
        out_dir=processed_dir,
        execution_trace_path=trace_path,
        post_processing_manifest_path=manifest_path,
        execution_log_path=log_path,
        object_registry_path=registry_path,
    )

    assert report["failed_cases"] == ["C081"]
    assert (raw_dir / "EBAN.csv").read_text(encoding="utf-8") == raw_before
    processed_rows = _read_csv(processed_dir / "EBAN.csv")
    assert processed_rows == [
        {
            "BADAT": "06/01/2026",
            "BANFN": "0010000317",
            "BEDAT": "06/01/2026",
            "ERDAT": "06/01/2026",
            "ERNAM": "inventory_manager_mi00_SYN",
            "LFDAT": "06/15/2026",
        }
    ]
    provenance_rows = _read_csv(processed_dir / "provenance.csv")
    assert any(row["field"] == "LFDAT" and row["raw_value"] == "06/02/2026" for row in provenance_rows)
    validation = json.loads((processed_dir / "validation-report.json").read_text(encoding="utf-8"))
    assert validation["errors"] == []


def test_projection_stats_flags_partial_field_coverage(tmp_path: Path) -> None:
    processed_dir = tmp_path / "processed"
    processed_dir.mkdir()
    _write_csv(
        processed_dir / "EBAN.csv",
        [
            {"BANFN": "10000317", "BADAT": "06/01/2026", "BEDAT": "06/01/2026"},
            {"BANFN": "10000318", "BADAT": "06/02/2026", "BEDAT": "06/02/2026"},
        ],
    )

    stats = _projection_stats(
        processed_dir,
        [
            {"table": "EBAN", "field": "BADAT"},
            {"table": "EBAN", "field": "BEDAT"},
            {"table": "EBAN", "field": "BEDAT"},
        ],
    )

    assert stats["missing_fields"] == {"EBAN": ["BADAT"]}


def test_process_dataset_requires_planned_synthetic_end(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    processed_dir = tmp_path / "processed"
    raw_dir.mkdir()
    _write_csv(raw_dir / "EBAN.csv", [{"BANFN": "0010000317", "BADAT": "06/01/2026"}])
    trace_path = tmp_path / "trace.yaml"
    manifest_path = tmp_path / "manifest.yaml"
    log_path = tmp_path / "log.jsonl"
    registry_path = tmp_path / "registry.jsonl"
    trace_path.write_text(
        yaml.safe_dump(
            {
                "run_id": "RUN_BA-210",
                "dependency_graph": {
                    "planned_steps": [
                        {
                            "planned_step_id": "C005_A1",
                            "case_id": "C005",
                            "step_type": "create_purchase_requisition",
                            "tool_name": "fiori.create_purchase_requisition",
                            "synthetic_actor_id": "inventory_manager_mi00",
                            "technical_sap_user_id": "TU_01",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "run_id": "RUN_BA-210",
                "actor_projection": [],
                "planned_step_timestamps": [{"planned_step_id": "C005_A1", "case_id": "C005"}],
            }
        ),
        encoding="utf-8",
    )
    log_path.write_text("", encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "case_id": "C005",
                "planned_step_id": "C005_A1",
                "object_type": "purchase_requisition",
                "keys": {"pr_number": "10000317"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="C005_A1.*planned_synthetic_end"):
        process_dataset(
            raw_dir=raw_dir,
            out_dir=processed_dir,
            execution_trace_path=trace_path,
            post_processing_manifest_path=manifest_path,
            execution_log_path=log_path,
            object_registry_path=registry_path,
        )


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))
