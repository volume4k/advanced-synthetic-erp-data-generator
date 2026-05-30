from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from erp_sap_export.artifacts import (
    build_linkage_index,
    derive_execution_window,
    load_jsonl,
)
from erp_sap_export.cli import _batched_cdpos_requests_from_cdhdr, _resolve_download_dir, _write_table_csvs
from erp_sap_export.specs import SelectionRange


def test_derive_execution_window_uses_log_timestamps_with_padding(tmp_path: Path) -> None:
    log_path = tmp_path / "run.execution-log.jsonl"
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"timestamp": "2026-05-21T08:00:00+00:00", "event_type": "run_started"}),
                json.dumps({"timestamp": "2026-05-21T08:42:10+00:00", "event_type": "planned_step_succeeded"}),
                json.dumps({"event_type": "missing_timestamp"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    window = derive_execution_window(log_path, padding_minutes=30)

    assert window.start == datetime(2026, 5, 21, 7, 30, tzinfo=UTC)
    assert window.end == datetime(2026, 5, 21, 9, 12, 10, tzinfo=UTC)


def test_load_jsonl_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "objects.jsonl"
    path.write_text('{"case_id":"C001"}\n\n{"case_id":"C002"}\n', encoding="utf-8")

    assert load_jsonl(path) == [{"case_id": "C001"}, {"case_id": "C002"}]


def test_resolve_download_dir_uses_manifest_run_id_by_default(tmp_path: Path) -> None:
    download_dir = _resolve_download_dir(None, {"run_id": "RUN_EXAMPLE_001"}, downloads_root=tmp_path)

    assert download_dir == tmp_path / "RUN_EXAMPLE_001"


def test_resolve_download_dir_keeps_explicit_out_dir(tmp_path: Path) -> None:
    explicit = tmp_path / "custom-output"

    assert _resolve_download_dir(explicit, {"run_id": "RUN_EXAMPLE_001"}, downloads_root=tmp_path) == explicit


def test_write_table_csvs_writes_tables_directly_under_run_folder(tmp_path: Path) -> None:
    _write_table_csvs(
        tmp_path,
        {"CDHDR": [{"USERNAME": "LEARN-801", "OBJECTID": "4500000138"}], "CDPOS": []},
        tables=["CDHDR", "CDPOS"],
    )

    assert (tmp_path / "CDHDR.csv").read_text(encoding="utf-8") == "OBJECTID,USERNAME\n4500000138,LEARN-801\n"
    assert (tmp_path / "CDPOS.csv").exists()
    assert not (tmp_path / "raw").exists()


def test_batched_cdpos_requests_group_change_number_ranges_by_object_class() -> None:
    rows = [
        {"OBJECTCLAS": "BANF", "OBJECTID": "0010000172", "CHANGENR": "0000734602"},
        {"OBJECTCLAS": "BANF", "OBJECTID": "0010000173", "CHANGENR": "0000734610"},
        {"OBJECTCLAS": "EINKBELEG", "OBJECTID": "4500000138", "CHANGENR": "0000734604"},
    ]

    requests = _batched_cdpos_requests_from_cdhdr(rows)

    assert [(item.table, item.selection) for item in requests] == [
        ("CDPOS", [SelectionRange("OBJECTCLAS", "BANF"), SelectionRange("CHANGENR", "0000734602", "0000734610")]),
        ("CDPOS", [SelectionRange("OBJECTCLAS", "EINKBELEG"), SelectionRange("CHANGENR", "0000734604")]),
    ]


def test_build_linkage_index_maps_registry_objects_to_sap_tables() -> None:
    registry_entries = [
        {
            "case_id": "C001",
            "planned_step_id": "C001_A2",
            "tool": "fiori.create_purchase_order",
            "synthetic_actor_id": "buyer_mi00",
            "technical_sap_user_id": "TU_02",
            "object_type": "purchase_order",
            "keys": {"po_number": "4500000138"},
        },
        {
            "case_id": "C001",
            "planned_step_id": "C001_A4",
            "tool": "fiori.create_supplier_invoice",
            "synthetic_actor_id": "ap_mi00",
            "technical_sap_user_id": "TU_04",
            "object_type": "supplier_invoice",
            "keys": {"invoice_number": "5105600133", "fiscal_year": "2026"},
        },
        {
            "case_id": "C001",
            "planned_step_id": "C001_A5",
            "tool": "fiori.send_payment",
            "synthetic_actor_id": "ap_mi00",
            "technical_sap_user_id": "TU_04",
            "object_type": "payment_document",
            "keys": {"payment_document_number": "1500000028"},
        },
    ]
    trace_steps = {
        "C001_A5": {"inputs": {"company_code": "US00"}},
    }

    index = build_linkage_index(registry_entries, trace_steps)

    assert index.find("EKKO", {"EBELN": "4500000138"}).case_id == "C001"
    assert index.find("EKPO", {"EBELN": "4500000138"}).planned_step_id == "C001_A2"
    assert index.find("RBKP", {"BELNR": "5105600133", "GJAHR": "2026"}).tool == "fiori.create_supplier_invoice"
    assert index.find("BSEG", {"BELNR": "1500000028", "BUKRS": "US00"}).tool == "fiori.send_payment"
    assert index.find("EKKO", {"EBELN": "9999999999"}) is None


def test_linkage_index_matches_sap_zero_padded_numeric_keys() -> None:
    registry_entries = [
        {
            "case_id": "C001",
            "planned_step_id": "C001_A1",
            "tool": "fiori.create_purchase_requisition",
            "synthetic_actor_id": "inventory_manager_mi00",
            "technical_sap_user_id": "TU_01",
            "object_type": "purchase_requisition",
            "keys": {"pr_number": "10000172"},
        },
    ]

    index = build_linkage_index(registry_entries, {})

    assert index.find("EBAN", {"BANFN": "0010000172"}).case_id == "C001"
