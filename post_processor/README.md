# Post-Processor

This folder owns Post-Processor requirements and the first SAP export helper.

`erp-sap-export` reads SAP tables through SAP GUI for HTML / WebGUI transaction `SE16` over HTTPS. It does not require RFC, SAP GUI desktop access, VPN access, ABAP reports, or SAP-side changes.

## SAP Table Export CLI

Install browser dependencies once:

```bash
uv run --project post_processor playwright install chromium
```

Probe WebGUI/SE16 capability:

```bash
uv run --project post_processor erp-sap-export probe \
  --execution-trace trace_generator/build/RUN.execution-trace.yaml \
  --env-file configuration/.env
```

Download raw CSVs for one execution run:

```bash
uv run --project post_processor erp-sap-export download \
  --execution-trace trace_generator/build/RUN.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN.post-processing-manifest.yaml \
  --execution-log trace_generator/build/RUN.execution-log.jsonl \
  --object-registry trace_generator/build/RUN.object-registry.jsonl \
  --env-file configuration/.env \
  --user-from LEARN-800 \
  --user-to LEARN-899 \
  --window-padding-min 30 \
  --max-keys-per-batch 20 \
  --max-runtime-min 60
```

By default, downloads are written to `post_processor/downloads/<run_id>/`, where `run_id` comes from the Post-Processing Manifest. Use `--out-dir PATH` to override that destination while keeping the same file layout.
The command logs each SAP phase, request count, filter summary, row count, and elapsed time to stdout. `--max-keys-per-batch` keeps range requests small enough for WebGUI list extraction while avoiding one request per object. `--max-runtime-min` stops scheduling new WebGUI requests after the budget is reached, writes any rows collected so far, records a warning in `export-report.json`, and exits with code `124`; use `0` to disable the guard.

Outputs:

- `<TABLE>.csv`: SAP rows parsed from WebGUI `SE16` list output, written directly under the run download folder.
- `export-report.json`: run id, real extraction window, user range, table counts, warnings.
- `row-linkage.csv`: SAP row keys linked to process cases, planned steps, actors, technical SAP users, and SAP transaction code when available.

Supported tables:

- `CDHDR`, filtered by `USERNAME`, `UDATE`, and same-day `UTIME`.
- `CDPOS`, derived from `CDHDR` change numbers through batched `OBJECTCLAS` / `CHANGENR` ranges and exact local composite-key post-filtering.
- `EBAN`, `EKKO`, `EKPO`, `MKPF`, `MSEG`, `RBKP`, `RSEG`, `BKPF`, `BSEG`, derived from Object Registry keys through batched table ranges and exact local object-key post-filtering.

The extraction window comes from real `Execution Log` timestamps plus padding. Planned Synthetic Time remains chronology truth for the final Synthetic Dataset, but raw SAP extraction must use physical SAP write time.

## Development

Run default tests:

```bash
uv run --project post_processor pytest post_processor/tests -q
```

Live SAP smoke tests are marked `live_sap` and excluded by default:

```bash
uv run --project post_processor pytest post_processor/tests -m live_sap -q
```

## Implementation Notes

- RFC was not used because direct SAP GUI/RFC access is blocked outside the UCC network.
- OData catalog access works, but the available change-document service is not sufficient for user-filtered raw `CDHDR`/`CDPOS` exports.
- Native WebGUI file download is not required. The CLI parses `.lsAbapList__item` DOM positions and writes local CSVs.
- Large tables that trigger the `Felder für Selektion auswählen` prompt are handled by continuing to the generated selection screen before applying filters.

## Chronology Contract

- The final ML-facing **Synthetic Dataset** uses **Planned Synthetic Time** as chronology truth.
- SAP export timestamp fields must be replaced through **Synthetic Timestamp Projection**.
- SAP physical write order may differ from final dataset order.
- Raw SAP runtime timestamps may be kept only in provenance or debug output, not as ML-facing chronology fields.
- SAP document numbers and object keys stay unchanged and remain join identifiers.

## Inputs

- **Execution Trace**: process cases, planned steps, planned synthetic time, actor sessions, inputs, labels, and required SAP object keys.
- **Post-Processing Manifest**: timestamp policy, planned step timestamps, actor projection, object lineage, planned date input overrides, and failed-case policy.
- **Execution Evidence**: execution log and object registry from the Trace Executor.
- **SAP Exports**: table extracts such as purchase orders, material documents, supplier invoices, accounting documents, CDHDR, and CDPOS.

## Required Behavior

- Exclude **Failed Process Cases** using Execution Evidence and Object Registry, not timestamp windows alone.
- Join SAP export rows to **Process Cases** through SAP object keys from the Object Registry.
- Replace table-specific date/time columns with the matching planned synthetic timestamp for the associated **Planned Step**.
- Preserve within-case process order from the Execution Trace.
- Preserve original technical SAP users in provenance output.
- Project synthetic actor identity according to actor projection rules.
- Keep planned business dates distinct from technical timestamps.

## Open Implementation Decisions

- Define table-specific timestamp fields and key fields for each SAP export.
- Define provenance table/file shape for raw SAP timestamps and technical SAP users.
- Define how to handle SAP exports where one row combines data from multiple planned steps.
- Define validation reports for missing object-registry joins, missing timestamp projections, and order violations.
