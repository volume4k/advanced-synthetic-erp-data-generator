# Post-Processor

`post_processor/` is an independent `uv` project that downloads raw SAP table data and processes it into the final **Synthetic Dataset**. Its CLI, `erp-sap-export`, reads SAP tables through SAP GUI for HTML / WebGUI transaction `SE16` over HTTPS.

The Post-Processor does not require RFC, SAP GUI desktop access, VPN access, ABAP reports, or SAP-side changes.

For the end-to-end dataset workflow, start with [the user guide](../docs/user-guide/README.md). This README is the local reference for SAP export download, processing, validation, and chronology rules.

## Component Role

Inputs:

- **Execution Trace** from the Trace Generator
- **Post-Processing Manifest** from the Trace Generator
- **Execution Evidence** from the Trace Executor
- raw **SAP Exports** downloaded through `erp-sap-export download`

Outputs:

- raw CSVs under `post_processor/downloads/<run_id>/`
- processed CSVs under `post_processor/processed/<run_id>/`
- `export-report.json`
- `processing-report.json`
- `validation-report.json`
- `provenance.csv`
- `row-linkage.csv`

The Post-Processor does not generate traces, execute Browser Tools, or call the realism LLM.

## Bootstrap

```bash
uv sync --project post_processor --python 3.13
uv run --project post_processor playwright install chromium
```

## CLI Overview

| Command | Purpose |
|---|---|
| `probe` | Validate SAP WebGUI and SE16 access. |
| `download` | Download raw SAP table CSVs for one execution run. |
| `process` | Create processed dataset CSVs from raw run downloads. |
| `validate-processed` | Validate processed outputs against raw exports, planned artifacts, and execution evidence. |

## Probe SAP Access

Probe WebGUI/SE16 capability:

```bash
uv run --project post_processor erp-sap-export probe \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env
```

When `--execution-trace` is supplied, the command uses the first Actor Session credentials from the trace and env file. Without `--execution-trace`, probe requires `SAP_USER_1_UN`, `SAP_USER_1_PW`, and `SAP_URL` in the env file.

Use `--headed` when login, launchpad, or WebGUI behavior needs manual inspection.

## Download SAP Exports

Download raw CSVs for one execution run:

```bash
uv run --project post_processor erp-sap-export download \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml \
  --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl \
  --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl \
  --env-file configuration/.env \
  --out-dir post_processor/downloads/RUN_EXAMPLE \
  --user-from LEARN-800 \
  --user-to LEARN-899 \
  --window-padding-min 30 \
  --max-keys-per-batch 20 \
  --max-runtime-min 60
```

By default, downloads are written to `post_processor/downloads/<run_id>/`, where `run_id` comes from the Post-Processing Manifest. Use `--out-dir PATH` to override that destination while keeping the same file layout.

The command logs each SAP phase, request count, filter summary, row count, and elapsed time to stdout.

Runtime guard:

- `--max-runtime-min` stops scheduling new WebGUI requests after the budget is reached.
- Collected rows are still written.
- A warning is recorded in `export-report.json`.
- The command exits with code `124`.
- Use `0` to disable the guard.

Batching:

- `--max-keys-per-batch` keeps range requests small enough for WebGUI list extraction while avoiding one request per object.
- `--max-rows-per-request` caps individual SE16 requests.
- `--cdhdr-window-min` splits CDHDR requests into time chunks.

Partial refresh:

- `--tables` can download a subset of supported tables.
- If `CDPOS` is requested without `CDHDR`, the command also requests `CDHDR` because CDPOS keys are derived from CDHDR.
- `row-linkage.csv` is regenerated only during a full table refresh. Partial refreshes preserve existing linkage and mark the report as partial.

## Process Downloads

Raw downloads stay in place and are not mutated by processing:

```bash
uv run --project post_processor erp-sap-export process \
  --raw-dir post_processor/downloads/RUN_EXAMPLE \
  --out-dir post_processor/processed/RUN_EXAMPLE \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml \
  --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl \
  --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

The command prints a processing report as JSON and exits with code `1` if validation errors are present in the report.

## Validate Processed Outputs

```bash
uv run --project post_processor erp-sap-export validate-processed \
  --processed-dir post_processor/processed/RUN_EXAMPLE \
  --raw-dir post_processor/downloads/RUN_EXAMPLE \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml \
  --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl \
  --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

The command prints a validation report as JSON and exits with code `1` when validation errors are found.

## Supported Tables

Change-document tables:

- `CDHDR`, filtered by `USERNAME`, `UDATE`, and same-day `UTIME`.
- `CDPOS`, derived from `CDHDR` change numbers through batched `OBJECTCLAS` / `CHANGENR` ranges and exact local composite-key post-filtering.

Procure-to-pay business tables derived from Object Registry keys:

- `EBAN`
- `EKKO`
- `EKPO`
- `MKPF`
- `MSEG`
- `RBKP`
- `RSEG`
- `BKPF`
- `BSEG`

The extraction window comes from real Execution Log timestamps plus padding. Planned Synthetic Time remains chronology truth for the final Synthetic Dataset, but raw SAP extraction must use physical SAP write time.

## Outputs

Raw download folder:

- `<TABLE>.csv`: SAP rows parsed from WebGUI `SE16` list output.
- `export-report.json`: run id, extraction window, user range, table counts, warnings, partial-refresh status.
- `row-linkage.csv`: SAP row keys linked to Process Cases, Planned Steps, actors, technical SAP users, and SAP transaction code when available.

Processed folder:

- processed table CSVs
- `processing-report.json`
- `validation-report.json`
- `provenance.csv`
- `row-linkage.csv`

## Chronology Contract

- The final ML-facing **Synthetic Dataset** uses **Planned Synthetic Time** as chronology truth.
- SAP export timestamp fields must be replaced through **Synthetic Timestamp Projection**.
- SAP physical write order may differ from final dataset order.
- Raw SAP runtime timestamps may be kept only in provenance or debug output, not as ML-facing chronology fields.
- SAP document numbers and object keys stay unchanged and remain join identifiers.

## Required Processing Behavior

- Exclude **Failed Process Cases** using Execution Evidence and Object Registry, not timestamp windows alone.
- Join SAP export rows to **Process Cases** through SAP object keys from the Object Registry.
- Replace table-specific date/time columns with the matching planned synthetic timestamp for the associated **Planned Step**.
- Preserve within-case process order from the Execution Trace.
- Preserve original technical SAP users in provenance output.
- Project synthetic actor identity according to actor projection rules.
- Keep planned business dates distinct from technical timestamps.

## Field Policy

| Table | Post-processing |
|---|---|
| `EBAN` | Rewrite `BADAT`, `BEDAT`, `ERDAT`; set `LFDAT` from `delivery_date`; rewrite `ERNAM` to projected actor. |
| `EKKO` | Rewrite `AEDAT`, `BEDAT`, `LASTCHANGEDATETIME`; rewrite `ERNAM` to projected actor. |
| `EKPO` | Rewrite `AEDAT`, `PRDAT`. |
| `MKPF` | Rewrite `BLDAT`, `BUDAT`, `CPUDT`, `CPUTM`; rewrite `USNAM` to projected actor. |
| `MSEG` | Filter/link only. |
| `RBKP` | Rewrite `BLDAT`, `CPUDT`, `CPUTM`; rewrite `USNAM` to projected actor. |
| `RSEG` | Filter/link only. |
| `BKPF` | Rewrite `BLDAT`, `BUDAT`, `CPUDT`, `CPUTM`; rewrite `USNAM` to projected actor. |
| `BSEG` | Rewrite non-zero `AUGDT`, `AUGCP`, `VALUT`. |
| `CDHDR` | Rewrite `UDATE`, `UTIME`; rewrite `USERNAME` to projected actor. |
| `CDPOS` | Filter/link only. |

## Timezone Notes

`CDHDR.UDATE/UTIME` can be mixed by object class in WebGUI export data. The current implementation treats `BUPA_BUP` as UTC and the default SAP change-document timezone as `Europe/Berlin`. Business document `CPUDT/CPUTM` fields are projected to planned synthetic business time in `Europe/Berlin`.

## Troubleshooting

- **SE16 cannot be reached**: run `probe --headed` and confirm WebGUI opens the table display transaction.
- **Selection-field prompt appears**: large tables can trigger `Felder fuer Selektion auswaehlen`; the CLI continues to the generated selection screen before applying filters.
- **No rows found**: check the Execution Log window, `--window-padding-min`, user range, and Object Registry keys.
- **CDPOS rows missing**: ensure CDHDR was downloaded for the same window; CDPOS requests are derived from CDHDR composite keys.
- **Partial refresh looks incomplete**: run a full refresh when `row-linkage.csv` or `export-report.json` should represent all tables.
- **Processed validation fails**: inspect `validation-report.json`, failed process case policy, and provenance timestamp projection.

## Testing

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

## Runbooks

- [RUN_BA-210](../docs/runs/BA-210.md)

## Related Documentation

- [User guide](../docs/user-guide/README.md)
- [Create a dataset](../docs/user-guide/create-dataset.md)
- [Configuration reference](../configuration/README.md)
- [Trace Generator reference](../trace_generator/README.md)
- [Trace Executor reference](../trace_executor/README.md)
