"""Post-process raw SAP exports into synthetic datasets."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from erp_sap_export.artifacts import (
    LinkageIndex,
    LinkageRecord,
    build_linkage_index,
    load_jsonl,
    load_yaml,
    trace_steps_by_id,
)
from erp_sap_export.specs import SUPPORTED_TABLES


CDHDR_UTC_OBJECT_CLASSES = {"BUPA_BUP"}
SAP_LOCAL_TIMEZONE = "Europe/Berlin"
REWRITE_FIELDS = {
    "EBAN": ("BADAT", "BEDAT", "ERDAT", "LFDAT", "ERNAM"),
    "EKKO": ("AEDAT", "BEDAT", "LASTCHANGEDATETIME", "ERNAM"),
    "EKPO": ("AEDAT", "PRDAT"),
    "MKPF": ("BLDAT", "BUDAT", "CPUDT", "CPUTM", "USNAM"),
    "RBKP": ("BLDAT", "CPUDT", "CPUTM", "USNAM"),
    "BKPF": ("BLDAT", "BUDAT", "CPUDT", "CPUTM", "USNAM"),
    "BSEG": ("AUGDT", "AUGCP", "VALUT"),
    "CDHDR": ("UDATE", "UTIME", "USERNAME"),
}


@dataclass(frozen=True)
class StepProjection:
    planned_step_id: str
    case_id: str
    step_type: str
    planned_synthetic_end: datetime
    planned_date_inputs: dict[str, str]
    expose_as: str
    synthetic_actor_id: str
    technical_sap_user_id: str
    tool: str
    object_type: str


@dataclass(frozen=True)
class BankChangeRecord:
    vendor_id: str
    success_at: datetime
    link: LinkageRecord


def process_dataset(
    *,
    raw_dir: Path,
    out_dir: Path,
    execution_trace_path: Path,
    post_processing_manifest_path: Path,
    execution_log_path: Path,
    object_registry_path: Path,
) -> dict[str, Any]:
    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    trace = load_yaml(execution_trace_path)
    manifest = load_yaml(post_processing_manifest_path)
    execution_log = load_jsonl(execution_log_path)
    registry_entries = load_jsonl(object_registry_path)
    trace_steps = trace_steps_by_id(trace)
    failed_cases = _failed_cases(execution_log)
    raw_checksums_before = _csv_checksums(raw_dir)

    index = build_linkage_index(registry_entries, trace_steps, default_company_code="US00")
    projections = _step_projections(trace_steps, manifest, registry_entries)
    address_index = _address_linkage(raw_dir, index)
    purchase_order_by_pr = _purchase_order_by_pr(registry_entries, index)
    cdhdr_links = _change_document_links(
        raw_dir,
        trace_steps,
        manifest,
        execution_log,
        address_index,
        purchase_order_by_pr,
        index,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    provenance_rows: list[dict[str, str]] = []
    processed_linkage_rows: list[dict[str, str]] = []
    table_reports: dict[str, dict[str, int]] = {}

    for table in SUPPORTED_TABLES:
        raw_path = raw_dir / f"{table}.csv"
        if not raw_path.exists():
            continue
        rows, fieldnames = _read_csv(raw_path)
        processed_rows: list[dict[str, str]] = []
        dropped_failed = 0
        dropped_unlinked = 0
        for row in rows:
            link = _find_link(table, row, index, cdhdr_links)
            if link is None:
                dropped_unlinked += 1
                continue
            if link.case_id in failed_cases:
                dropped_failed += 1
                continue
            projection = projections.get(link.planned_step_id)
            if projection is None:
                dropped_unlinked += 1
                continue
            processed = dict(row)
            _apply_projection(table, processed, projection, provenance_rows)
            processed_rows.append(processed)
            processed_linkage_rows.append(_linkage_row(table, processed, link))
        _write_csv(out_dir / f"{table}.csv", processed_rows, fieldnames)
        table_reports[table] = {
            "raw_rows": len(rows),
            "processed_rows": len(processed_rows),
            "dropped_failed_case_rows": dropped_failed,
            "dropped_unlinked_rows": dropped_unlinked,
        }

    processed_linkage_rows.sort(key=_linkage_sort_key(trace_steps))
    _write_csv(out_dir / "row-linkage.csv", processed_linkage_rows, _linkage_fieldnames())
    _write_csv(out_dir / "provenance.csv", provenance_rows, _provenance_fieldnames())
    raw_checksums_after = _csv_checksums(raw_dir)
    validation = _validation_report(
        processed_linkage_rows,
        failed_cases=failed_cases,
        raw_checksums_before=raw_checksums_before,
        raw_checksums_after=raw_checksums_after,
        registry_entries=registry_entries,
        raw_dir=raw_dir,
        processed_dir=out_dir,
        provenance_rows=provenance_rows,
        trace_steps=trace_steps,
    )
    report = {
        "run_id": str(manifest.get("run_id") or trace.get("run_id") or ""),
        "raw_dir": str(raw_dir),
        "processed_dir": str(out_dir),
        "failed_cases": sorted(failed_cases),
        "tables": table_reports,
        "raw_checksums": raw_checksums_before,
        "validation": validation,
    }
    (out_dir / "processing-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "validation-report.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def validate_processed_dataset(
    *,
    processed_dir: Path,
    raw_dir: Path,
    execution_trace_path: Path,
    post_processing_manifest_path: Path,
    execution_log_path: Path,
    object_registry_path: Path,
) -> dict[str, Any]:
    processed_dir = Path(processed_dir)
    raw_dir = Path(raw_dir)
    trace = load_yaml(execution_trace_path)
    trace_steps = trace_steps_by_id(trace)
    load_yaml(post_processing_manifest_path)
    failed_cases = _failed_cases(load_jsonl(execution_log_path))
    registry_entries = load_jsonl(object_registry_path)
    linkage_rows, _fields = _read_csv(processed_dir / "row-linkage.csv")
    provenance_rows, _provenance_fields = _read_csv(processed_dir / "provenance.csv")
    report_path = processed_dir / "processing-report.json"
    raw_checksums_before = {}
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        raw_checksums_before = report.get("raw_checksums") if isinstance(report.get("raw_checksums"), dict) else {}
    validation = _validation_report(
        linkage_rows,
        failed_cases=failed_cases,
        raw_checksums_before=raw_checksums_before,
        raw_checksums_after=_csv_checksums(raw_dir),
        registry_entries=registry_entries,
        raw_dir=raw_dir,
        processed_dir=processed_dir,
        provenance_rows=provenance_rows,
        trace_steps=trace_steps,
    )
    (processed_dir / "validation-report.json").write_text(
        json.dumps(validation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return validation


def _failed_cases(execution_log: list[dict[str, Any]]) -> set[str]:
    failed: set[str] = set()
    for event in execution_log:
        if event.get("event_type") in {"case_failed", "planned_step_failed"} and event.get("case_id"):
            failed.add(str(event["case_id"]))
    return failed


def _step_projections(
    trace_steps: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    registry_entries: list[dict[str, Any]],
) -> dict[str, StepProjection]:
    actor_exposure = {
        str(item.get("synthetic_actor_id")): str(item.get("expose_as") or item.get("synthetic_actor_id") or "")
        for item in manifest.get("actor_projection", [])
        if isinstance(item, dict)
    }
    registry_object_types = {
        str(item.get("planned_step_id")): str(item.get("object_type") or "")
        for item in registry_entries
        if isinstance(item, dict) and item.get("planned_step_id")
    }
    output: dict[str, StepProjection] = {}
    for item in manifest.get("planned_step_timestamps", []):
        if not isinstance(item, dict) or not item.get("planned_step_id"):
            continue
        planned_step_id = str(item["planned_step_id"])
        planned_synthetic_end = item.get("planned_synthetic_end")
        if not planned_synthetic_end:
            raise ValueError(f"planned_step_timestamps entry {planned_step_id} is missing planned_synthetic_end")
        step = trace_steps.get(planned_step_id, {})
        synthetic_actor = str(step.get("synthetic_actor_id") or "")
        output[planned_step_id] = StepProjection(
            planned_step_id=planned_step_id,
            case_id=str(item.get("case_id") or step.get("case_id") or ""),
            step_type=str(item.get("step_type") or step.get("step_type") or ""),
            planned_synthetic_end=_parse_datetime(str(planned_synthetic_end)),
            planned_date_inputs=dict(item.get("planned_date_inputs") or step.get("planned_date_inputs") or {}),
            expose_as=actor_exposure.get(synthetic_actor, synthetic_actor),
            synthetic_actor_id=synthetic_actor,
            technical_sap_user_id=str(step.get("technical_sap_user_id") or ""),
            tool=str(step.get("tool_name") or ""),
            object_type=registry_object_types.get(planned_step_id, ""),
        )
    return output


def _find_link(
    table: str,
    row: dict[str, str],
    index: LinkageIndex,
    cdhdr_links: dict[tuple[str, str, str], LinkageRecord],
) -> LinkageRecord | None:
    table = table.upper()
    if table == "CDHDR":
        key = _change_key(row)
        return cdhdr_links.get(key)
    if table == "CDPOS":
        key = _change_key(row)
        return cdhdr_links.get(key)
    return index.find(table, row)


def _apply_projection(
    table: str,
    row: dict[str, str],
    projection: StepProjection,
    provenance_rows: list[dict[str, str]],
) -> None:
    table = table.upper()
    if table == "EBAN":
        for field in ("BADAT", "BEDAT", "ERDAT"):
            _rewrite(row, table, field, _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        if "delivery_date" in projection.planned_date_inputs:
            _rewrite(row, table, "LFDAT", _sap_date_from_iso(projection.planned_date_inputs["delivery_date"]), projection, provenance_rows)
        _rewrite(row, table, "ERNAM", projection.expose_as, projection, provenance_rows)
    elif table == "EKKO":
        for field in ("AEDAT", "BEDAT"):
            _rewrite(row, table, field, _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "LASTCHANGEDATETIME", _sap_decimal_timestamp(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "ERNAM", projection.expose_as, projection, provenance_rows)
    elif table == "EKPO":
        for field in ("AEDAT", "PRDAT"):
            _rewrite(row, table, field, _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
    elif table == "MKPF":
        document_date = projection.planned_date_inputs.get("document_date")
        _rewrite(
            row,
            table,
            "BLDAT",
            _sap_date_from_iso(document_date) if document_date else _sap_date(projection.planned_synthetic_end),
            projection,
            provenance_rows,
        )
        if "posting_date" in projection.planned_date_inputs:
            _rewrite(row, table, "BUDAT", _sap_date_from_iso(projection.planned_date_inputs["posting_date"]), projection, provenance_rows)
        _rewrite(row, table, "CPUDT", _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "CPUTM", _sap_time(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "USNAM", projection.expose_as, projection, provenance_rows)
    elif table == "RBKP":
        if "invoice_date" in projection.planned_date_inputs:
            _rewrite(row, table, "BLDAT", _sap_date_from_iso(projection.planned_date_inputs["invoice_date"]), projection, provenance_rows)
        _rewrite(row, table, "CPUDT", _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "CPUTM", _sap_time(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "USNAM", projection.expose_as, projection, provenance_rows)
    elif table == "BKPF":
        if "posting_document_date" in projection.planned_date_inputs:
            _rewrite(row, table, "BLDAT", _sap_date_from_iso(projection.planned_date_inputs["posting_document_date"]), projection, provenance_rows)
        if "posting_date" in projection.planned_date_inputs:
            _rewrite(row, table, "BUDAT", _sap_date_from_iso(projection.planned_date_inputs["posting_date"]), projection, provenance_rows)
        _rewrite(row, table, "CPUDT", _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "CPUTM", _sap_time(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "USNAM", projection.expose_as, projection, provenance_rows)
    elif table == "BSEG" and "posting_date" in projection.planned_date_inputs:
        value = _sap_date_from_iso(projection.planned_date_inputs["posting_date"])
        for field in ("AUGDT", "AUGCP", "VALUT"):
            if row.get(field) and row.get(field) != "00/00/0000":
                _rewrite(row, table, field, value, projection, provenance_rows)
    elif table == "CDHDR":
        _rewrite(row, table, "UDATE", _sap_date(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "UTIME", _sap_time(projection.planned_synthetic_end), projection, provenance_rows)
        _rewrite(row, table, "USERNAME", projection.expose_as, projection, provenance_rows)


def _rewrite(
    row: dict[str, str],
    table: str,
    field: str,
    value: str,
    projection: StepProjection,
    provenance_rows: list[dict[str, str]],
) -> None:
    if field not in row:
        return
    raw_value = row.get(field, "")
    row[field] = value
    provenance_rows.append(
        {
            "table": table,
            "field": field,
            "case_id": projection.case_id,
            "planned_step_id": projection.planned_step_id,
            "raw_value": raw_value,
            "synthetic_value": value,
            "reason": "synthetic_timestamp_projection",
        }
    )


def _address_linkage(raw_dir: Path, index: LinkageIndex) -> dict[str, LinkageRecord]:
    output: dict[str, LinkageRecord] = {}
    for table in ("EBAN", "EKPO"):
        path = raw_dir / f"{table}.csv"
        if not path.exists():
            continue
        rows, _fields = _read_csv(path)
        for row in rows:
            address = _normalize_key(row.get("ADRNR"))
            if not address:
                continue
            record = index.find(table, row)
            if record is not None:
                output[address] = record
    return output


def _change_document_links(
    raw_dir: Path,
    trace_steps: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    execution_log: list[dict[str, Any]],
    address_index: dict[str, LinkageRecord],
    purchase_order_by_pr: dict[str, LinkageRecord],
    index: LinkageIndex,
) -> dict[tuple[str, str, str], LinkageRecord]:
    path = raw_dir / "CDHDR.csv"
    if not path.exists():
        return {}
    rows, _fields = _read_csv(path)
    bank_records = _bank_change_records(trace_steps, manifest, execution_log)
    output: dict[tuple[str, str, str], LinkageRecord] = {}
    for row in rows:
        record = _change_document_link(row, address_index, bank_records, purchase_order_by_pr, index)
        if record is not None:
            output[_change_key(row)] = record
    return output


def _change_document_link(
    row: dict[str, str],
    address_index: dict[str, LinkageRecord],
    bank_records: dict[str, list[BankChangeRecord]],
    purchase_order_by_pr: dict[str, LinkageRecord],
    index: LinkageIndex,
) -> LinkageRecord | None:
    object_class = str(row.get("OBJECTCLAS") or "")
    object_id = str(row.get("OBJECTID") or "")
    if object_class == "ADRESSE" and object_id.startswith("ME02"):
        return address_index.get(_normalize_key(object_id[4:]))
    if object_class == "BANF":
        pr_number = _normalize_key(object_id)
        if str(row.get("TCODE") or "") == "ME21N":
            return purchase_order_by_pr.get(pr_number)
        return index.find("EBAN", {"BANFN": pr_number})
    if object_class == "EINKBELEG":
        return index.find("EKKO", {"EBELN": _normalize_key(object_id)})
    if object_class == "KRED":
        return _closest_bank_record(row, bank_records, sap_timezone=SAP_LOCAL_TIMEZONE)
    if object_class == "BUPA_BUP":
        return _closest_bank_record(row, bank_records, sap_timezone="UTC")
    return None


def _bank_change_records(
    trace_steps: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
    execution_log: list[dict[str, Any]],
) -> dict[str, list[BankChangeRecord]]:
    projections = _step_projections(trace_steps, manifest, [])
    success_order = {
        str(event.get("planned_step_id")): index
        for index, event in enumerate(execution_log)
        if event.get("event_type") == "planned_step_succeeded" and event.get("planned_step_id")
    }
    success_at = {
        str(event.get("planned_step_id")): _parse_datetime(str(event.get("timestamp") or ""))
        for event in execution_log
        if event.get("event_type") == "planned_step_succeeded" and event.get("planned_step_id") and event.get("timestamp")
    }
    output: dict[str, list[BankChangeRecord]] = {}
    for step_id, step in trace_steps.items():
        if step.get("step_type") not in {"change_vendor_bank_data", "revert_vendor_bank_data"}:
            continue
        inputs = step.get("inputs") if isinstance(step.get("inputs"), dict) else {}
        vendor_id = _normalize_key(inputs.get("vendor_id"))
        projection = projections.get(step_id)
        if not vendor_id or projection is None or step_id not in success_at:
            continue
        output.setdefault(vendor_id, []).append(
            BankChangeRecord(
                vendor_id=vendor_id,
                success_at=success_at[step_id],
                link=LinkageRecord(
                    table="CDHDR",
                    key=(),
                    case_id=projection.case_id,
                    planned_step_id=step_id,
                    tool=projection.tool,
                    synthetic_actor_id=projection.synthetic_actor_id,
                    technical_sap_user_id=projection.technical_sap_user_id,
                    object_type="vendor_bank_change",
                ),
            )
        )
    for records in output.values():
        records.sort(key=lambda item: success_order.get(item.link.planned_step_id, 1_000_000))
    return output


def _closest_bank_record(
    row: dict[str, str],
    bank_records: dict[str, list[BankChangeRecord]],
    *,
    sap_timezone: str,
) -> LinkageRecord | None:
    candidates = bank_records.get(_normalize_key(row.get("OBJECTID")), [])
    changed_at = _change_datetime(row, sap_timezone=sap_timezone)
    if not candidates or changed_at is None:
        return None
    match = min(candidates, key=lambda item: abs((item.success_at - changed_at).total_seconds()))
    if abs((match.success_at - changed_at).total_seconds()) > 180:
        return None
    return match.link


def _purchase_order_by_pr(
    registry_entries: list[dict[str, Any]],
    index: LinkageIndex,
) -> dict[str, LinkageRecord]:
    output: dict[str, LinkageRecord] = {}
    for entry in registry_entries:
        if entry.get("object_type") != "purchase_order":
            continue
        keys = entry.get("keys") if isinstance(entry.get("keys"), dict) else {}
        po_number = keys.get("po_number")
        if not po_number:
            continue
        record = index.find("EKKO", {"EBELN": po_number})
        if record is None:
            continue
        parents = entry.get("parent_references") if isinstance(entry.get("parent_references"), list) else []
        for parent in parents:
            if not isinstance(parent, dict):
                continue
            if parent.get("object_type") == "purchase_requisition" and parent.get("key") == "pr_number":
                output[_normalize_key(parent.get("value"))] = record
    return output


def _change_datetime(row: dict[str, str], *, sap_timezone: str) -> datetime | None:
    date_text = str(row.get("UDATE") or "").strip()
    time_text = str(row.get("UTIME") or "").strip()
    if not date_text or not time_text:
        return None
    try:
        local = datetime.strptime(f"{date_text} {time_text}", "%m/%d/%Y %H:%M:%S").replace(tzinfo=ZoneInfo(sap_timezone))
    except ValueError:
        return None
    return local.astimezone(UTC)


def _linkage_sort_key(trace_steps: dict[str, dict[str, Any]]):
    step_order = {step_id: index for index, step_id in enumerate(trace_steps)}
    table_order = {table: index for index, table in enumerate(SUPPORTED_TABLES)}

    def sort_key(row: dict[str, str]) -> tuple[str, int, int, str]:
        return (
            str(row.get("case_id") or ""),
            step_order.get(str(row.get("planned_step_id") or ""), 1_000_000),
            table_order.get(str(row.get("table") or ""), 1_000_000),
            str(row.get("row_key") or ""),
        )

    return sort_key


def _change_document_key_match(
    registry_entries: list[dict[str, Any]],
    raw_dir: Path,
) -> dict[str, dict[str, int]]:
    expected = {"BANF": set(), "EINKBELEG": set()}
    for entry in registry_entries:
        keys = entry.get("keys") if isinstance(entry.get("keys"), dict) else {}
        if entry.get("object_type") == "purchase_requisition" and keys.get("pr_number"):
            expected["BANF"].add(_normalize_key(keys["pr_number"]))
        elif entry.get("object_type") == "purchase_order" and keys.get("po_number"):
            expected["EINKBELEG"].add(_normalize_key(keys["po_number"]))
    actual = {"BANF": set(), "EINKBELEG": set()}
    path = raw_dir / "CDHDR.csv"
    if not path.exists():
        return {}
    rows, _fields = _read_csv(path)
    for row in rows:
        object_class = str(row.get("OBJECTCLAS") or "")
        if object_class in actual:
            actual[object_class].add(_normalize_key(row.get("OBJECTID")))
    output: dict[str, dict[str, int]] = {}
    for object_class in sorted(expected):
        output[object_class] = {
            "registry_keys": len(expected[object_class]),
            "raw_keys": len(actual[object_class]),
            "raw_matches": len(expected[object_class] & actual[object_class]),
            "stale_raw_keys": len(actual[object_class] - expected[object_class]),
            "missing_registry_keys": len(expected[object_class] - actual[object_class]),
        }
    return output


def _projection_stats(processed_dir: Path, provenance_rows: list[dict[str, str]]) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    counts = {f"{table}.{field}": 0 for table, fields in REWRITE_FIELDS.items() for field in fields}
    for row in provenance_rows:
        key = f"{row.get('table')}.{row.get('field')}"
        if key in counts:
            counts[key] += 1
    for table, fields in REWRITE_FIELDS.items():
        path = processed_dir / f"{table}.csv"
        if not path.exists():
            continue
        rows, fieldnames = _read_csv(path)
        if not rows:
            continue
        for field in fields:
            if field not in fieldnames:
                continue
            needed_count = _projection_needed_count(table, field, rows)
            if needed_count and counts[f"{table}.{field}"] < needed_count:
                missing.setdefault(table, []).append(field)
    return {"counts": counts, "missing_fields": missing}


def _projection_needed_count(table: str, field: str, rows: list[dict[str, str]]) -> int:
    if table == "BSEG":
        return sum(1 for row in rows if str(row.get(field) or "") not in {"", "0", "00/00/0000"})
    return sum(1 for row in rows if field in row)


def _process_order_stats(
    linkage_rows: list[dict[str, str]],
    trace_steps: dict[str, dict[str, Any]],
) -> dict[str, int]:
    step_order = {step_id: index for index, step_id in enumerate(trace_steps)}
    last_by_case: dict[str, int] = {}
    violations = 0
    for row in linkage_rows:
        case_id = str(row.get("case_id") or "")
        order = step_order.get(str(row.get("planned_step_id") or ""), 1_000_000)
        previous = last_by_case.get(case_id, -1)
        if order < previous:
            violations += 1
        last_by_case[case_id] = max(previous, order)
    return {"rows": len(linkage_rows), "violations": violations}


def _validation_report(
    linkage_rows: list[dict[str, str]],
    *,
    failed_cases: set[str],
    raw_checksums_before: dict[str, str],
    raw_checksums_after: dict[str, str],
    registry_entries: list[dict[str, Any]],
    raw_dir: Path,
    processed_dir: Path,
    provenance_rows: list[dict[str, str]],
    trace_steps: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    failed_link_rows = [row for row in linkage_rows if row.get("case_id") in failed_cases]
    if failed_link_rows:
        errors.append(f"Processed linkage contains {len(failed_link_rows)} failed-case rows")
    if raw_checksums_before and raw_checksums_before != raw_checksums_after:
        errors.append("Raw CSV checksums changed during processing")
    key_match = _registry_raw_key_match(registry_entries, raw_dir)
    for object_type, stats in key_match.items():
        if stats["registry_keys"] and not stats["raw_matches"]:
            errors.append(f"No raw table key matches for registry object type {object_type}")
    if not provenance_rows:
        warnings.append("No provenance rows were written")
    change_document_key_match = _change_document_key_match(registry_entries, raw_dir)
    for object_class, stats in change_document_key_match.items():
        if stats["stale_raw_keys"]:
            errors.append(f"CDHDR {object_class} contains {stats['stale_raw_keys']} stale raw keys")
        if stats["missing_registry_keys"]:
            errors.append(f"CDHDR {object_class} is missing {stats['missing_registry_keys']} registry keys")
    projection_stats = _projection_stats(processed_dir, provenance_rows)
    for table, fields in projection_stats["missing_fields"].items():
        if fields:
            errors.append(f"Missing timestamp projection for {table}: {', '.join(fields)}")
    order_stats = _process_order_stats(linkage_rows, trace_steps)
    if order_stats["violations"]:
        errors.append(f"Processed row-linkage has {order_stats['violations']} process-order violations")
    return {
        "errors": errors,
        "warnings": warnings,
        "failed_cases": sorted(failed_cases),
        "registry_raw_key_match": key_match,
        "change_document_key_match": change_document_key_match,
        "projection_stats": projection_stats,
        "process_order": order_stats,
    }


def _registry_raw_key_match(registry_entries: list[dict[str, Any]], raw_dir: Path) -> dict[str, dict[str, int]]:
    raw_keys = _raw_key_sets(raw_dir)
    field_by_type = {
        "purchase_requisition": "pr_number",
        "purchase_order": "po_number",
        "material_document": "material_document_number",
        "stock_release_material_document": "material_document_number",
        "scrap_material_document": "material_document_number",
        "supplier_invoice": "invoice_number",
        "payment_document": "payment_document_number",
    }
    table_key_by_type = {
        "purchase_requisition": "EBAN",
        "purchase_order": "EKKO",
        "material_document": "MKPF",
        "stock_release_material_document": "MKPF",
        "scrap_material_document": "MKPF",
        "supplier_invoice": "RBKP",
        "payment_document": "BKPF",
    }
    output: dict[str, dict[str, int]] = {}
    for entry in registry_entries:
        object_type = str(entry.get("object_type") or "")
        field = field_by_type.get(object_type)
        table = table_key_by_type.get(object_type)
        keys = entry.get("keys") if isinstance(entry.get("keys"), dict) else {}
        if not field or not table or not keys.get(field):
            continue
        value = _normalize_key(keys[field])
        stats = output.setdefault(object_type, {"registry_keys": 0, "raw_matches": 0})
        stats["registry_keys"] += 1
        if value in raw_keys.get(table, set()):
            stats["raw_matches"] += 1
    return output


def _raw_key_sets(raw_dir: Path) -> dict[str, set[str]]:
    fields = {"EBAN": "BANFN", "EKKO": "EBELN", "MKPF": "MBLNR", "RBKP": "BELNR", "BKPF": "BELNR"}
    output: dict[str, set[str]] = {}
    for table, field in fields.items():
        path = raw_dir / f"{table}.csv"
        if not path.exists():
            continue
        rows, _fieldnames = _read_csv(path)
        output[table] = {_normalize_key(row.get(field)) for row in rows if row.get(field)}
    return output


def _linkage_row(table: str, row: dict[str, str], link: LinkageRecord) -> dict[str, str]:
    return {
        "table": table.upper(),
        "row_key": _row_key(table, row),
        "case_id": link.case_id,
        "planned_step_id": link.planned_step_id,
        "tool": link.tool,
        "synthetic_actor_id": link.synthetic_actor_id,
        "technical_sap_user_id": link.technical_sap_user_id,
        "object_type": link.object_type,
        "sap_tcode": str(row.get("TCODE") or row.get("TCODE2") or ""),
    }


def _row_key(table: str, row: dict[str, str]) -> str:
    table = table.upper()
    fields_by_table = {
        "EBAN": ("BANFN",),
        "EKKO": ("EBELN",),
        "EKPO": ("EBELN", "EBELP"),
        "MKPF": ("MBLNR", "MJAHR"),
        "MSEG": ("MBLNR", "MJAHR", "ZEILE"),
        "RBKP": ("BELNR", "GJAHR"),
        "RSEG": ("BELNR", "GJAHR", "BUZEI"),
        "BKPF": ("BELNR", "BUKRS", "GJAHR"),
        "BSEG": ("BELNR", "BUKRS", "GJAHR", "BUZEI"),
        "CDHDR": ("OBJECTCLAS", "OBJECTID", "CHANGENR"),
        "CDPOS": ("OBJECTCLAS", "OBJECTID", "CHANGENR", "TABNAME", "FNAME"),
    }
    return "|".join(f"{field}={_normalize_key(row.get(field))}" for field in fields_by_table.get(table, ()) if row.get(field))


def _change_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        str(row.get("OBJECTCLAS") or ""),
        str(row.get("OBJECTID") or ""),
        str(row.get("CHANGENR") or ""),
    )


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def _write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _csv_checksums(path: Path) -> dict[str, str]:
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.glob("*.csv"))
    }


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sap_date(value: datetime) -> str:
    return value.strftime("%m/%d/%Y")


def _sap_time(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def _sap_date_from_iso(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%m/%d/%Y")


def _sap_decimal_timestamp(value: datetime) -> str:
    digits = value.strftime("%Y%m%d%H%M%S")
    groups: list[str] = []
    while digits:
        groups.append(digits[-3:])
        digits = digits[:-3]
    return f"{','.join(reversed(groups))}.0000000"


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return str(int(text))
    return text


def _linkage_fieldnames() -> list[str]:
    return [
        "case_id",
        "object_type",
        "planned_step_id",
        "row_key",
        "sap_tcode",
        "synthetic_actor_id",
        "table",
        "technical_sap_user_id",
        "tool",
    ]


def _provenance_fieldnames() -> list[str]:
    return [
        "case_id",
        "field",
        "planned_step_id",
        "raw_value",
        "reason",
        "synthetic_value",
        "table",
    ]
