"""SE16 table request specs and filter builders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


SUPPORTED_TABLES = ["CDHDR", "CDPOS", "EBAN", "EKKO", "EKPO", "MKPF", "MSEG", "RBKP", "RSEG", "BKPF", "BSEG"]


@dataclass(frozen=True)
class SelectionRange:
    field: str
    low: str
    high: str | None = None


@dataclass(frozen=True)
class TableRequest:
    table: str
    selection: list[SelectionRange]
    max_rows: int | None = None


FIELD_TITLES = {
    "BANFN": ["Bestellanforderung"],
    "BELNR": ["Belegnummer", "Rechnungsbelegnummer"],
    "BUKRS": ["Buchungskreis"],
    "CHANGENR": ["Änderungsnummer des Belegs"],
    "EBELN": ["Einkaufsbeleg"],
    "GJAHR": ["Geschäftsjahr"],
    "MJAHR": ["Materialbelegjahr"],
    "MBLNR": ["Materialbeleg"],
    "OBJECTCLAS": ["Objektklasse"],
    "OBJECTID": ["Objektwert"],
    "UDATE": ["Erstellungsdatum des Änderungsbelegs"],
    "USERNAME": ["Benutzername des Änderers im Änderungsbeleg"],
    "UTIME": ["Uhrzeit der Änderung"],
}


def cdhdr_selection(
    *,
    start: datetime,
    end: datetime,
    user_from: str,
    user_to: str,
) -> list[SelectionRange]:
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    ranges = [
        SelectionRange("USERNAME", user_from, user_to),
        SelectionRange("UDATE", _sap_date(start_utc), _sap_date(end_utc)),
    ]
    if start_utc.date() == end_utc.date():
        ranges.append(SelectionRange("UTIME", _sap_time(start_utc), _sap_time(end_utc)))
    return ranges


def cdpos_requests_from_cdhdr(rows: list[dict[str, Any]]) -> list[TableRequest]:
    seen: set[tuple[str, str, str]] = set()
    requests: list[TableRequest] = []
    for row in rows:
        key = (str(row.get("OBJECTCLAS") or ""), str(row.get("OBJECTID") or ""), str(row.get("CHANGENR") or ""))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        requests.append(
            TableRequest(
                "CDPOS",
                [
                    SelectionRange("OBJECTCLAS", key[0]),
                    SelectionRange("OBJECTID", key[1]),
                    SelectionRange("CHANGENR", key[2]),
                ],
            )
        )
    return requests


def p2p_requests_from_registry(
    registry_entries: list[dict[str, Any]],
    trace_steps: dict[str, dict[str, Any]],
    *,
    default_company_code: str | None = None,
) -> list[TableRequest]:
    requests: list[TableRequest] = []
    seen: set[tuple[str, tuple[SelectionRange, ...]]] = set()
    for entry in registry_entries:
        for request in _requests_for_registry_entry(entry, trace_steps, default_company_code):
            key = (request.table, tuple(request.selection))
            if key in seen:
                continue
            seen.add(key)
            requests.append(request)
    return requests


def _requests_for_registry_entry(
    entry: dict[str, Any],
    trace_steps: dict[str, dict[str, Any]],
    default_company_code: str | None,
) -> list[TableRequest]:
    object_type = str(entry.get("object_type") or "")
    keys = entry.get("keys") if isinstance(entry.get("keys"), dict) else {}
    company_code = _company_code(entry, trace_steps, default_company_code)
    if object_type == "purchase_requisition" and keys.get("pr_number"):
        return [TableRequest("EBAN", [SelectionRange("BANFN", str(keys["pr_number"]))])]
    if object_type == "purchase_order" and keys.get("po_number"):
        po = str(keys["po_number"])
        return [
            TableRequest("EKKO", [SelectionRange("EBELN", po)]),
            TableRequest("EKPO", [SelectionRange("EBELN", po)]),
        ]
    if object_type in {"material_document", "scrap_material_document", "stock_release_material_document"} and keys.get(
        "material_document_number"
    ):
        number = str(keys["material_document_number"])
        return [
            TableRequest("MKPF", [SelectionRange("MBLNR", number)]),
            TableRequest("MSEG", [SelectionRange("MBLNR", number)]),
        ]
    if object_type == "supplier_invoice" and keys.get("invoice_number") and keys.get("fiscal_year"):
        selection = [
            SelectionRange("BELNR", str(keys["invoice_number"])),
            SelectionRange("GJAHR", str(keys["fiscal_year"])),
        ]
        return [TableRequest("RBKP", selection), TableRequest("RSEG", selection)]
    if object_type == "payment_document" and keys.get("payment_document_number"):
        selection = [SelectionRange("BELNR", str(keys["payment_document_number"]))]
        if company_code:
            selection.append(SelectionRange("BUKRS", company_code))
        return [TableRequest("BKPF", selection), TableRequest("BSEG", selection)]
    return []


def _company_code(
    entry: dict[str, Any],
    trace_steps: dict[str, dict[str, Any]],
    default_company_code: str | None,
) -> str | None:
    planned_step_id = str(entry.get("planned_step_id") or "")
    step = trace_steps.get(planned_step_id) or {}
    inputs = step.get("inputs") if isinstance(step, dict) else None
    if isinstance(inputs, dict) and inputs.get("company_code"):
        return str(inputs["company_code"])
    return default_company_code


def _sap_date(value: datetime) -> str:
    return value.strftime("%m/%d/%Y")


def _sap_time(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
