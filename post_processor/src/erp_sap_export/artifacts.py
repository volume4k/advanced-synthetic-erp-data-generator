"""Execution artifact loading and SAP-row linkage helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ExecutionWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class LinkageRecord:
    table: str
    key: tuple[tuple[str, str], ...]
    case_id: str
    planned_step_id: str
    tool: str
    synthetic_actor_id: str
    technical_sap_user_id: str
    object_type: str


class LinkageIndex:
    def __init__(self, records: list[LinkageRecord]) -> None:
        self.records = records
        self._records_by_table_key = {(record.table, record.key): record for record in records}

    def find(self, table: str, row: dict[str, Any]) -> LinkageRecord | None:
        candidates = _candidate_row_keys(table, row)
        for key in candidates:
            record = self._records_by_table_key.get((table.upper(), key))
            if record is not None:
                return record
        return None


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} at line {line_number}: {exc}") from exc
        if isinstance(payload, dict):
            records.append(payload)
    return records


def derive_execution_window(path: str | Path, *, padding_minutes: int) -> ExecutionWindow:
    timestamps = [
        _parse_timestamp(record["timestamp"])
        for record in load_jsonl(path)
        if isinstance(record.get("timestamp"), str) and record["timestamp"]
    ]
    if not timestamps:
        raise ValueError(f"Execution log '{path}' contains no timestamp fields")
    padding = timedelta(minutes=padding_minutes)
    return ExecutionWindow(start=min(timestamps) - padding, end=max(timestamps) + padding)


def load_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_yaml(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected YAML object in '{path}'")
    return payload


def trace_steps_by_id(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    graph = trace.get("dependency_graph") or {}
    planned_steps = graph.get("planned_steps") or []
    return {
        str(step["planned_step_id"]): step
        for step in planned_steps
        if isinstance(step, dict) and step.get("planned_step_id")
    }


def first_actor_session_credentials(trace: dict[str, Any], env: dict[str, str]) -> tuple[str, str, str]:
    sessions = trace.get("actor_sessions") or []
    if not sessions or not isinstance(sessions[0], dict):
        raise ValueError("Execution trace has no actor_sessions entry for SAP login")
    session = sessions[0]
    username = env.get(str(session.get("username_env_var")))
    password = env.get(str(session.get("password_env_var")))
    login_url = env.get(str(session.get("login_url_env_var")))
    if not username or not password or not login_url:
        raise ValueError("Missing username/password/login URL env values for first actor session")
    return username, password, login_url


def build_linkage_index(
    registry_entries: list[dict[str, Any]],
    trace_steps: dict[str, dict[str, Any]],
    *,
    default_company_code: str | None = None,
) -> LinkageIndex:
    records: list[LinkageRecord] = []
    for entry in registry_entries:
        records.extend(_records_for_registry_entry(entry, trace_steps, default_company_code=default_company_code))
    return LinkageIndex(records)


def linkage_rows_for_table(table: str, rows: list[dict[str, Any]], index: LinkageIndex) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        record = index.find(table, row)
        if record is None:
            continue
        output.append(
            {
                "table": table.upper(),
                "row_key": _stringify_key(record.key),
                "case_id": record.case_id,
                "planned_step_id": record.planned_step_id,
                "tool": record.tool,
                "synthetic_actor_id": record.synthetic_actor_id,
                "technical_sap_user_id": record.technical_sap_user_id,
                "object_type": record.object_type,
                "sap_tcode": str(row.get("TCODE") or ""),
            }
        )
    return output


def _records_for_registry_entry(
    entry: dict[str, Any],
    trace_steps: dict[str, dict[str, Any]],
    *,
    default_company_code: str | None,
) -> list[LinkageRecord]:
    object_type = str(entry.get("object_type") or "")
    keys = entry.get("keys") if isinstance(entry.get("keys"), dict) else {}
    planned_step_id = str(entry.get("planned_step_id") or "")
    company_code = _company_code_for_step(trace_steps.get(planned_step_id), default_company_code)
    table_keys = _table_keys_for_object(object_type, keys, company_code)
    records: list[LinkageRecord] = []
    for table, key in table_keys:
        records.append(
            LinkageRecord(
                table=table,
                key=key,
                case_id=str(entry.get("case_id") or ""),
                planned_step_id=planned_step_id,
                tool=str(entry.get("tool") or ""),
                synthetic_actor_id=str(entry.get("synthetic_actor_id") or ""),
                technical_sap_user_id=str(entry.get("technical_sap_user_id") or ""),
                object_type=object_type,
            )
        )
    return records


def _table_keys_for_object(
    object_type: str,
    keys: dict[str, Any],
    company_code: str | None,
) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    if object_type == "purchase_requisition" and keys.get("pr_number"):
        return [("EBAN", _key(BANFN=keys["pr_number"]))]
    if object_type == "purchase_order" and keys.get("po_number"):
        return [
            ("EKKO", _key(EBELN=keys["po_number"])),
            ("EKPO", _key(EBELN=keys["po_number"])),
        ]
    if object_type in {"material_document", "scrap_material_document", "stock_release_material_document"} and keys.get(
        "material_document_number"
    ):
        return [
            ("MKPF", _key(MBLNR=keys["material_document_number"])),
            ("MSEG", _key(MBLNR=keys["material_document_number"])),
        ]
    if object_type == "supplier_invoice" and keys.get("invoice_number") and keys.get("fiscal_year"):
        return [
            ("RBKP", _key(BELNR=keys["invoice_number"], GJAHR=keys["fiscal_year"])),
            ("RSEG", _key(BELNR=keys["invoice_number"], GJAHR=keys["fiscal_year"])),
        ]
    if object_type == "payment_document" and keys.get("payment_document_number"):
        base = {"BELNR": keys["payment_document_number"]}
        if company_code:
            base["BUKRS"] = company_code
        return [
            ("BKPF", _key(**base)),
            ("BSEG", _key(**base)),
        ]
    return []


def _candidate_row_keys(table: str, row: dict[str, Any]) -> list[tuple[tuple[str, str], ...]]:
    table = table.upper()
    if table == "EBAN":
        return [_key(BANFN=row.get("BANFN"))]
    if table in {"EKKO", "EKPO"}:
        return [_key(EBELN=row.get("EBELN"))]
    if table in {"MKPF", "MSEG"}:
        return [_key(MBLNR=row.get("MBLNR"))]
    if table in {"RBKP", "RSEG"}:
        return [_key(BELNR=row.get("BELNR"), GJAHR=row.get("GJAHR"))]
    if table in {"BKPF", "BSEG"}:
        candidates = [_key(BELNR=row.get("BELNR"), BUKRS=row.get("BUKRS"), GJAHR=row.get("GJAHR"))]
        candidates.append(_key(BELNR=row.get("BELNR"), BUKRS=row.get("BUKRS")))
        candidates.append(_key(BELNR=row.get("BELNR")))
        return candidates
    return []


def _company_code_for_step(step: dict[str, Any] | None, default_company_code: str | None) -> str | None:
    inputs = step.get("inputs") if isinstance(step, dict) else None
    if isinstance(inputs, dict) and inputs.get("company_code"):
        return str(inputs["company_code"])
    return default_company_code


def _key(**values: Any) -> tuple[tuple[str, str], ...]:
    return tuple((key, str(value)) for key, value in values.items() if value not in {None, ""})


def _stringify_key(key: tuple[tuple[str, str], ...]) -> str:
    return "|".join(f"{name}={value}" for name, value in key)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
