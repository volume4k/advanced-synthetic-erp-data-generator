"""CLI for SAP WebGUI table exports."""

from __future__ import annotations

import argparse
import csv
import json
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from erp_sap_export.artifacts import (
    ExecutionWindow,
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
from erp_sap_export.specs import (
    SUPPORTED_TABLES,
    SelectionRange,
    TableRequest,
    cdhdr_selection,
    cdpos_requests_from_cdhdr,
    p2p_batched_requests_from_registry,
    p2p_requests_from_registry,
)


DEFAULT_TABLES = SUPPORTED_TABLES
POST_PROCESSOR_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOWNLOADS_DIR = POST_PROCESSOR_ROOT / "downloads"


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
    return 0 if _probe_result_ok(result) else 1


def _download(args: argparse.Namespace) -> int:
    started_at = time.monotonic()
    deadline = _deadline(started_at, args.max_runtime_min)
    requested_tables = _requested_tables(args.tables)
    if "CDPOS" in requested_tables and "CDHDR" not in requested_tables:
        requested_tables = ["CDHDR", *requested_tables]
    trace = load_yaml(args.execution_trace)
    manifest = load_yaml(args.post_processing_manifest)
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
    run_id = _run_id_from_manifest(manifest)

    out_dir = _resolve_download_dir(args.out_dir, manifest)
    out_dir.mkdir(parents=True, exist_ok=True)
    _log(f"run={run_id} output_dir={out_dir}")
    _log(f"tables={','.join(requested_tables)}")
    _log(f"window={window.start.isoformat()}..{window.end.isoformat()} user_range={args.user_from}..{args.user_to}")
    if deadline is not None:
        _log(f"runtime_guard={args.max_runtime_min:g}min")

    client = Se16Client(credentials, headed=args.headed)
    report: dict[str, Any] = {
        "run_id": run_id,
        "download_dir": str(out_dir),
        "execution_window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "user_range": {"from": args.user_from, "to": args.user_to},
        "tables": {},
        "warnings": [],
    }
    trace_run_id = trace.get("run_id")
    if trace_run_id and str(trace_run_id) != run_id:
        report["warnings"].append(f"Trace run_id {trace_run_id} differs from manifest run_id {run_id}")

    rows_by_table: dict[str, list[dict[str, str]]] = defaultdict(list)
    timed_out = False
    cdhdr_rows: list[dict[str, str]] = []
    if "CDHDR" in requested_tables:
        cdhdr_requests = _cdhdr_requests(
            window,
            user_from=args.user_from,
            user_to=args.user_to,
            max_rows_per_request=args.max_rows_per_request,
            chunk_minutes=args.cdhdr_window_min,
        )
        _log(f"CDHDR chunks={len(cdhdr_requests)} chunk_minutes={args.cdhdr_window_min:g}")
        cdhdr_results = _extract_requests(
            client,
            cdhdr_requests,
            "CDHDR",
            started_at=started_at,
            deadline=deadline,
            fresh_page_per_request=True,
        )
        if len(cdhdr_results) < len(cdhdr_requests):
            timed_out = True
            warning = f"Runtime guard stopped CDHDR after {len(cdhdr_results)}/{len(cdhdr_requests)} chunks"
            report["warnings"].append(warning)
            _log(warning)
        for rows in cdhdr_results:
            cdhdr_rows.extend(
                _post_filter_cdhdr(
                    rows,
                    user_from=args.user_from,
                    user_to=args.user_to,
                    start=window.start,
                    end=window.end,
                )
            )
        cdhdr_rows = _dedupe_rows(cdhdr_rows)
        _log(f"CDHDR done rows={len(cdhdr_rows)} elapsed={_elapsed(started_at)}")
        rows_by_table["CDHDR"].extend(cdhdr_rows)
        report["tables"]["CDHDR"] = {
            "chunks": len(cdhdr_results),
            "planned_chunks": len(cdhdr_requests),
            "rows": len(cdhdr_rows),
            "selection": [
                [asdict(item) for item in request.selection]
                for request in cdhdr_requests
            ],
            "timestamp_timezone": "UTC",
        }

    if "CDPOS" in requested_tables:
        cdpos_rows: list[dict[str, str]] = []
        exact_cdpos_requests = cdpos_requests_from_cdhdr(cdhdr_rows)
        cdpos_requests = [
            TableRequest(request.table, request.selection, max_rows=args.max_rows_per_request)
            for request in _batched_cdpos_requests_from_cdhdr(cdhdr_rows)
        ]
        _log(f"CDPOS exact_keys={len(exact_cdpos_requests)} batched_requests={len(cdpos_requests)}")
        cdpos_results = _extract_requests(
            client,
            cdpos_requests,
            "CDPOS",
            started_at=started_at,
            deadline=deadline,
            fresh_page_per_request=True,
        )
        if len(cdpos_results) < len(cdpos_requests):
            timed_out = True
            warning = f"Runtime guard stopped CDPOS after {len(cdpos_results)}/{len(cdpos_requests)} requests"
            report["warnings"].append(warning)
            _log(warning)
        for rows in cdpos_results:
            cdpos_rows.extend(_post_filter_cdpos(rows, cdhdr_rows))
        rows_by_table["CDPOS"].extend(_dedupe_rows(cdpos_rows))
        report["tables"]["CDPOS"] = {
            "source": "CDHDR composite keys",
            "exact_keys": len(exact_cdpos_requests),
            "requests": len(cdpos_results),
            "planned_requests": len(cdpos_requests),
            "rows": len(rows_by_table["CDPOS"]),
        }

    p2p_tables = [table for table in requested_tables if table not in {"CDHDR", "CDPOS"}]
    index = build_linkage_index(registry_entries, trace_steps, default_company_code=args.default_company_code)
    exact_p2p_requests = p2p_requests_from_registry(
        registry_entries,
        trace_steps,
        default_company_code=args.default_company_code,
    )
    p2p_requests = [
        TableRequest(request.table, request.selection, max_rows=args.max_rows_per_request)
        for request in p2p_batched_requests_from_registry(
            registry_entries,
            trace_steps,
            default_company_code=args.default_company_code,
            max_keys_per_batch=args.max_keys_per_batch,
        )
        if request.table in p2p_tables
    ]
    if p2p_tables:
        _log(
            "P2P "
            f"registry_entries={len(registry_entries)} exact_keys={len(exact_p2p_requests)} "
            f"max_keys_per_batch={args.max_keys_per_batch} batched_requests={len(p2p_requests)}"
        )

    for request_index, request in enumerate(p2p_requests, start=1):
        if _deadline_reached(deadline):
            timed_out = True
            warning = f"Runtime guard stopped P2P after {request_index - 1}/{len(p2p_requests)} requests"
            report["warnings"].append(warning)
            _log(warning)
            break
        request_started = time.monotonic()
        _log(f"P2P [{request_index}/{len(p2p_requests)}] {request.table} start filter={_selection_summary(request)}")
        try:
            candidate_rows = client.extract(request)
            rows = _post_filter_linked_rows(request.table, candidate_rows, index)
            rows_by_table[request.table].extend(rows)
            _log(
                f"P2P [{request_index}/{len(p2p_requests)}] {request.table} done "
                f"candidate_rows={len(candidate_rows)} linked_rows={len(rows)} "
                f"request_elapsed={_elapsed(request_started)} total_elapsed={_elapsed(started_at)}"
            )
        except RuntimeError as exc:
            report["warnings"].append(f"{request.table}: {exc}")
            _log(f"P2P [{request_index}/{len(p2p_requests)}] {request.table} warning={exc}")
        report["tables"].setdefault(request.table, {"requests": 0, "rows": 0})
        report["tables"][request.table]["requests"] += 1
        report["tables"][request.table]["selection"] = [asdict(item) for item in request.selection]

    for table in requested_tables:
        rows_by_table[table] = _dedupe_rows(rows_by_table.get(table, []))
        report["tables"].setdefault(table, {"requests": 0, "rows": 0})
        report["tables"][table]["rows"] = len(rows_by_table[table])
    report["timezone_validation"] = {
        "CDHDR.UDATE_UTIME": "UTC",
        "business.CPUDT_CPUTM": "Europe/Berlin",
    }
    full_refresh = set(requested_tables) == set(DEFAULT_TABLES)
    _write_table_csvs(out_dir, rows_by_table, tables=requested_tables)
    _log(f"wrote table CSVs count={len(requested_tables)} dir={out_dir}")

    if full_refresh:
        linkage_rows: list[dict[str, str]] = []
        for table, rows in rows_by_table.items():
            linkage_rows.extend(linkage_rows_for_table(table, rows, index))
        _write_csv(out_dir / "row-linkage.csv", linkage_rows)
        report["row_linkage_written"] = True
        _log(f"wrote row-linkage.csv rows={len(linkage_rows)}")
    else:
        report["row_linkage_written"] = False
        report["partial_refresh"] = {"tables": requested_tables}
        _log("preserved row-linkage.csv for partial table refresh")
    report_to_write = _report_for_write(out_dir, report, requested_tables)
    (out_dir / "export-report.json").write_text(
        json.dumps(report_to_write, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _log(f"wrote export-report.json elapsed={_elapsed(started_at)}")
    return 124 if timed_out else 0


def _run_id_from_manifest(manifest: dict[str, Any]) -> str:
    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("Post-processing manifest is missing required run_id")
    return run_id


def _resolve_download_dir(
    out_dir: Path | None,
    manifest: dict[str, Any],
    *,
    downloads_root: Path = DEFAULT_DOWNLOADS_DIR,
) -> Path:
    if out_dir is not None:
        return Path(out_dir)
    return downloads_root / _run_id_from_manifest(manifest)


def _write_table_csvs(
    out_dir: Path,
    rows_by_table: dict[str, list[dict[str, str]]],
    *,
    tables: Sequence[str] = DEFAULT_TABLES,
) -> None:
    for table in tables:
        _write_csv(out_dir / f"{table}.csv", rows_by_table.get(table, []))


def _cdhdr_requests(
    window: ExecutionWindow,
    *,
    user_from: str,
    user_to: str,
    max_rows_per_request: int | None,
    chunk_minutes: float,
) -> list[TableRequest]:
    if chunk_minutes <= 0:
        return [
            TableRequest(
                "CDHDR",
                cdhdr_selection(start=window.start, end=window.end, user_from=user_from, user_to=user_to),
                max_rows=max_rows_per_request,
            )
        ]
    requests: list[TableRequest] = []
    cursor = window.start
    step = timedelta(minutes=chunk_minutes)
    while cursor <= window.end:
        chunk_end = min(cursor + step, window.end)
        requests.append(
            TableRequest(
                "CDHDR",
                cdhdr_selection(start=cursor, end=chunk_end, user_from=user_from, user_to=user_to),
                max_rows=max_rows_per_request,
            )
        )
        if chunk_end >= window.end:
            break
        cursor = chunk_end + timedelta(seconds=1)
    return requests


def _extract_requests(
    client: Se16Client,
    requests: Sequence[TableRequest],
    phase: str,
    *,
    started_at: float,
    deadline: float | None,
    fresh_page_per_request: bool = False,
) -> list[list[dict[str, str]]]:
    request_started: dict[int, float] = {}

    def on_start(index: int, request: TableRequest) -> None:
        request_started[index] = time.monotonic()
        _log(f"{phase} [{index}/{len(requests)}] {request.table} start filter={_selection_summary(request)}")

    def on_done(index: int, request: TableRequest, rows: list[dict[str, str]]) -> None:
        _log(
            f"{phase} [{index}/{len(requests)}] {request.table} done "
            f"rows={len(rows)} request_elapsed={_elapsed(request_started[index])} total_elapsed={_elapsed(started_at)}"
        )

    if fresh_page_per_request:
        results: list[list[dict[str, str]]] = []
        for index, request in enumerate(requests, start=1):
            if _deadline_reached(deadline):
                break
            on_start(index, request)
            rows = client.extract(request)
            results.append(rows)
            on_done(index, request, rows)
        return results

    return client.extract_many(
        requests,
        on_start=on_start,
        on_done=on_done,
        should_continue=lambda: not _deadline_reached(deadline),
    )


def _batched_cdpos_requests_from_cdhdr(cdhdr_rows: list[dict[str, str]]) -> list[TableRequest]:
    changes_by_class: dict[str, set[str]] = defaultdict(set)
    for row in cdhdr_rows:
        object_class = str(row.get("OBJECTCLAS") or "")
        change_number = str(row.get("CHANGENR") or "")
        if object_class and change_number:
            changes_by_class[object_class].add(change_number)
    requests: list[TableRequest] = []
    for object_class in sorted(changes_by_class):
        change_numbers = sorted(changes_by_class[object_class])
        high = change_numbers[-1] if change_numbers[0] != change_numbers[-1] else None
        requests.append(
            TableRequest(
                "CDPOS",
                [
                    SelectionRange("OBJECTCLAS", object_class),
                    SelectionRange("CHANGENR", change_numbers[0], high),
                ],
            )
        )
    return requests


def _post_filter_linked_rows(table: str, rows: list[dict[str, str]], index) -> list[dict[str, str]]:
    return [row for row in rows if index.find(table, row) is not None]


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


def _post_filter_cdhdr(
    rows: list[dict[str, str]],
    *,
    user_from: str,
    user_to: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, str]]:
    start_utc = _as_utc(start)
    end_utc = _as_utc(end)
    output: list[dict[str, str]] = []
    for row in rows:
        username = str(row.get("USERNAME") or "")
        changed_at = _sap_change_datetime(row)
        if changed_at is None:
            continue
        if user_from <= username <= user_to and start_utc <= changed_at <= end_utc:
            output.append(row)
    return output


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


def _report_for_write(out_dir: Path, report: dict[str, Any], requested_tables: Sequence[str]) -> dict[str, Any]:
    if set(requested_tables) == set(DEFAULT_TABLES):
        return report
    existing_path = out_dir / "export-report.json"
    if not existing_path.exists():
        return report
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return report
    if not isinstance(existing, dict):
        return report
    return _merge_partial_report(existing, report, requested_tables)


def _merge_partial_report(
    existing: dict[str, Any],
    partial: dict[str, Any],
    requested_tables: Sequence[str],
) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in partial.items():
        if key not in {"tables", "warnings"}:
            merged[key] = value
    tables = dict(existing.get("tables") if isinstance(existing.get("tables"), dict) else {})
    partial_tables = partial.get("tables") if isinstance(partial.get("tables"), dict) else {}
    for table in requested_tables:
        if table in partial_tables:
            tables[table] = partial_tables[table]
    merged["tables"] = tables
    warnings: list[Any] = []
    for source in (existing.get("warnings"), partial.get("warnings")):
        if isinstance(source, list):
            warnings.extend(source)
    merged["warnings"] = warnings
    merged["partial_refresh"] = {"tables": list(requested_tables)}
    return merged


def _requested_tables(values: Sequence[str]) -> list[str]:
    requested: list[str] = []
    for value in values:
        table = value.upper()
        if table not in SUPPORTED_TABLES:
            raise ValueError(f"Unsupported table '{value}'")
        if table not in requested:
            requested.append(table)
    return requested


def _probe_result_ok(result: dict[str, Any]) -> bool:
    table_results = result.get("tables")
    if not result.get("webgui") or not result.get("se16") or not isinstance(table_results, dict):
        return False
    return all(_probe_table_ok(table_result) for table_result in table_results.values())


def _probe_table_ok(table_result: Any) -> bool:
    if not isinstance(table_result, dict):
        return False
    return bool(table_result.get("usable") or table_result.get("status") == "ok") or (
        bool(table_result.get("selection_screen"))
        and not table_result.get("not_authorized")
        and not table_result.get("open_failed")
        and not table_result.get("error")
    )


def _sap_change_datetime(row: dict[str, str]) -> datetime | None:
    date_text = str(row.get("UDATE") or "").strip()
    time_text = str(row.get("UTIME") or "").strip()
    if not date_text or not time_text:
        return None
    try:
        return datetime.strptime(f"{date_text} {time_text}", "%m/%d/%Y %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _deadline(started_at: float, max_runtime_min: float) -> float | None:
    if max_runtime_min <= 0:
        return None
    return started_at + (max_runtime_min * 60)


def _deadline_reached(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _selection_summary(request: TableRequest) -> str:
    return ",".join(
        f"{item.field}={item.low}" + (f"..{item.high}" if item.high is not None else "")
        for item in request.selection
    )


def _elapsed(started_at: float) -> str:
    elapsed = time.monotonic() - started_at
    if elapsed < 60:
        return f"{elapsed:.1f}s"
    minutes, seconds = divmod(elapsed, 60)
    return f"{int(minutes)}m{seconds:04.1f}s"


def _log(message: str) -> None:
    print(f"[sap-export] {message}", flush=True)


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
    download.add_argument("--out-dir", type=Path)
    download.add_argument("--user-from", default="LEARN-800")
    download.add_argument("--user-to", default="LEARN-899")
    download.add_argument("--window-padding-min", type=int, default=30)
    download.add_argument("--max-rows-per-request", type=int, default=5_000)
    download.add_argument("--max-keys-per-batch", type=int, default=20)
    download.add_argument("--cdhdr-window-min", type=float, default=15)
    download.add_argument("--max-runtime-min", type=float, default=60)
    download.add_argument("--default-company-code", default="US00")
    download.add_argument("--tables", nargs="+", default=DEFAULT_TABLES)
    download.add_argument("--headed", action="store_true")
    return parser
