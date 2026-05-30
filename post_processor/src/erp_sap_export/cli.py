"""CLI for SAP WebGUI table exports."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from erp_sap_export.artifacts import (
    build_linkage_index,
    derive_execution_window,
    first_actor_session_credentials,
    linkage_rows_for_table,
    load_env_file,
    load_jsonl,
    load_yaml,
    trace_steps_by_id,
)
from erp_sap_export.se16 import Se16Client, WebGuiCredentials, webgui_url_from_login_url
from erp_sap_export.specs import SUPPORTED_TABLES, TableRequest, cdhdr_selection, cdpos_requests_from_cdhdr, p2p_requests_from_registry


DEFAULT_TABLES = SUPPORTED_TABLES


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "probe":
        return _probe(args)
    if args.command == "download":
        return _download(args)
    parser.error("missing command")
    return 2


def _probe(args: argparse.Namespace) -> int:
    credentials = _credentials_from_args(args)
    client = Se16Client(credentials, headed=args.headed)
    result = client.probe(args.tables)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("webgui") and result.get("se16") else 1


def _download(args: argparse.Namespace) -> int:
    trace = load_yaml(args.execution_trace)
    _manifest = load_yaml(args.post_processing_manifest)
    env = load_env_file(args.env_file)
    username, password, login_url = first_actor_session_credentials(trace, env)
    credentials = WebGuiCredentials(
        username=username,
        password=password,
        webgui_url=webgui_url_from_login_url(login_url),
    )
    trace_steps = trace_steps_by_id(trace)
    registry_entries = load_jsonl(args.object_registry)
    window = derive_execution_window(args.execution_log, padding_minutes=args.window_padding_min)

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    client = Se16Client(credentials, headed=args.headed)
    report: dict[str, Any] = {
        "run_id": trace.get("run_id"),
        "execution_window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "user_range": {"from": args.user_from, "to": args.user_to},
        "tables": {},
        "warnings": [],
    }

    rows_by_table: dict[str, list[dict[str, str]]] = defaultdict(list)
    cdhdr_request = TableRequest(
        "CDHDR",
        cdhdr_selection(start=window.start, end=window.end, user_from=args.user_from, user_to=args.user_to),
        max_rows=args.max_rows_per_request,
    )
    cdhdr_rows = _post_filter_cdhdr(
        client.extract(cdhdr_request),
        user_from=args.user_from,
        user_to=args.user_to,
    )
    rows_by_table["CDHDR"].extend(cdhdr_rows)
    report["tables"]["CDHDR"] = _request_report(cdhdr_request, len(cdhdr_rows))

    cdpos_rows: list[dict[str, str]] = []
    for request in cdpos_requests_from_cdhdr(cdhdr_rows):
        request = TableRequest(request.table, request.selection, max_rows=args.max_rows_per_request)
        cdpos_rows.extend(_post_filter_cdpos(client.extract(request), cdhdr_rows))
    rows_by_table["CDPOS"].extend(_dedupe_rows(cdpos_rows))
    report["tables"]["CDPOS"] = {"source": "CDHDR composite keys", "rows": len(rows_by_table["CDPOS"])}

    p2p_requests = p2p_requests_from_registry(
        registry_entries,
        trace_steps,
        default_company_code=args.default_company_code,
    )
    for request in p2p_requests:
        request = TableRequest(request.table, request.selection, max_rows=args.max_rows_per_request)
        try:
            rows_by_table[request.table].extend(client.extract(request))
        except RuntimeError as exc:
            report["warnings"].append(f"{request.table}: {exc}")
        report["tables"].setdefault(request.table, {"requests": 0, "rows": 0})
        report["tables"][request.table]["requests"] += 1

    for table in DEFAULT_TABLES:
        rows_by_table[table] = _dedupe_rows(rows_by_table.get(table, []))
        report["tables"].setdefault(table, {"requests": 0, "rows": 0})
        report["tables"][table]["rows"] = len(rows_by_table[table])
        _write_csv(raw_dir / f"{table}.csv", rows_by_table[table])

    index = build_linkage_index(registry_entries, trace_steps, default_company_code=args.default_company_code)
    linkage_rows: list[dict[str, str]] = []
    for table, rows in rows_by_table.items():
        linkage_rows.extend(linkage_rows_for_table(table, rows, index))
    _write_csv(out_dir / "row-linkage.csv", linkage_rows)
    (out_dir / "export-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _credentials_from_args(args: argparse.Namespace) -> WebGuiCredentials:
    if args.execution_trace:
        trace = load_yaml(args.execution_trace)
        env = load_env_file(args.env_file)
        username, password, login_url = first_actor_session_credentials(trace, env)
    else:
        env = load_env_file(args.env_file)
        username = env.get("SAP_USER_1_UN")
        password = env.get("SAP_USER_1_PW")
        login_url = env.get("SAP_URL")
        if not username or not password or not login_url:
            raise ValueError("Probe without --execution-trace requires SAP_USER_1_UN, SAP_USER_1_PW, and SAP_URL")
    return WebGuiCredentials(username=username, password=password, webgui_url=webgui_url_from_login_url(login_url))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[tuple[str, str], ...]] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        key = tuple(sorted((str(k), str(v)) for k, v in row.items()))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def _post_filter_cdhdr(rows: list[dict[str, str]], *, user_from: str, user_to: str) -> list[dict[str, str]]:
    return [row for row in rows if user_from <= str(row.get("USERNAME") or "") <= user_to]


def _post_filter_cdpos(rows: list[dict[str, str]], cdhdr_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed = {
        (str(row.get("OBJECTCLAS") or ""), str(row.get("OBJECTID") or ""), str(row.get("CHANGENR") or ""))
        for row in cdhdr_rows
    }
    return [
        row
        for row in rows
        if (str(row.get("OBJECTCLAS") or ""), str(row.get("OBJECTID") or ""), str(row.get("CHANGENR") or "")) in allowed
    ]


def _request_report(request: TableRequest, row_count: int) -> dict[str, Any]:
    return {"selection": [asdict(item) for item in request.selection], "rows": row_count}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="erp-sap-export")
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Validate SAP WebGUI SE16 access")
    probe.add_argument("--execution-trace", type=Path)
    probe.add_argument("--env-file", type=Path, default=Path("configuration/.env"))
    probe.add_argument("--tables", nargs="+", default=["CDHDR", "CDPOS", "EBAN", "EKKO", "EKPO", "MKPF", "MSEG", "RBKP", "RSEG", "BKPF", "BSEG"])
    probe.add_argument("--headed", action="store_true")

    download = subparsers.add_parser("download", help="Download SAP tables for one execution run")
    download.add_argument("--execution-trace", type=Path, required=True)
    download.add_argument("--post-processing-manifest", type=Path, required=True)
    download.add_argument("--execution-log", type=Path, required=True)
    download.add_argument("--object-registry", type=Path, required=True)
    download.add_argument("--env-file", type=Path, default=Path("configuration/.env"))
    download.add_argument("--out-dir", type=Path, required=True)
    download.add_argument("--user-from", default="LEARN-800")
    download.add_argument("--user-to", default="LEARN-899")
    download.add_argument("--window-padding-min", type=int, default=30)
    download.add_argument("--max-rows-per-request", type=int, default=5_000)
    download.add_argument("--default-company-code", default="US00")
    download.add_argument("--headed", action="store_true")
    return parser
