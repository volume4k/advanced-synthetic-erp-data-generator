# Create a Dataset

This guide walks through one complete dataset workflow. It assumes prerequisites are complete and credentials live in `configuration/.env`.

Use a stable `<run_id>` for all generated artifacts. The examples below use `RUN_EXAMPLE`.

## 1. Configure the Run

Edit the Pkl files under `configuration/`:

- `actors.pkl`: Synthetic Actors, display names, roles, work locations, realism profiles, realism guardrails, and actor capabilities.
- `technical_users.pkl`: Technical SAP User references. Values are environment variable names, not secrets.
- `identity_mapping.pkl`: mapping from Synthetic Actors to Technical SAP Users.
- `master_data.pkl`: Configured Master Data for materials, vendors, plants, purchasing organizations, storage locations, quantity guardrails, prices, currencies, and delivery lead times.
- `processes.pkl`: Process Definitions, Process Steps, Browser Tool assignments, Input Bindings, planned date inputs, required SAP object keys, labels, runtime date overrides, and dependencies.
- `fraud_scenarios.pkl`: Case Scenario Type controls and scenario-specific values.
- `run_settings.pkl`: case count, run horizon, scheduler seed, active process types, working hours, timing ranges, post-processing export groups, and realism settings.

For realism-enabled runs, keep this enabled in `run_settings.pkl`:

```pkl
realism {
  enabled = true
}
```

Tune realism with hard guardrails in `actors.pkl`, `master_data.pkl`, and `run_settings.pkl`. The LLM can propose patterns only inside those bounds; the Trace Generator remains responsible for validation, exact case expansion, scheduling, and artifact writing.

## 2. Compile Configuration

Run:

```bash
configuration/create-config.sh
```

The Windows equivalent is 
```bash
.\configuration\create-config.ps1
```

This command:

- regenerates `configuration/generated_tool_config.pkl` from the Trace Executor registry
- formats and validates Pkl modules
- writes `configuration/build/main.yaml`

To write a custom compiled YAML path:

```bash
configuration/create-config.sh /tmp/main.yaml
```

## 3. Generate Planned Artifacts

Run the Trace Generator:

```bash
uv run --project trace_generator erp-trace-generate \
  configuration/build/main.yaml \
  --out-dir trace_generator/build/RUN_EXAMPLE \
  --run-id RUN_EXAMPLE \
  --env-file configuration/.env
```

WIN
```bash
uv run --project trace_generator erp-trace-generate configuration/build/main.yaml --out-dir trace_generator/build/RUN_EXAMPLE --run-id RUN_EXAMPLE --env-file configuration/.env
```

Expected outputs:

```text
trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml
trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml
```

If realism is enabled, validated compiler output is cached under `configuration/build/realism-criteria.<hash>.json`. Treat that cache as a performance detail; the Execution Trace and Post-Processing Manifest are the workflow artifacts needed downstream.

## 4. Execute the Trace in SAP

Run the Trace Executor:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env \
  --artifact-dir trace_executor/build/RUN_EXAMPLE \
  --log-level INFO
```

WIN
```bash
uv run --project trace_executor erp-trace-exec trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --env-file configuration/.env --artifact-dir trace_executor/build/RUN_EXAMPLE --log-level INFO
```

Use `--headed` while validating a run manually:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env \
  --artifact-dir trace_executor/build/RUN_EXAMPLE \
  --headed
```

WIN
```bash
uv run --project trace_executor erp-trace-exec trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --env-file configuration/.env --artifact-dir trace_executor/build/RUN_EXAMPLE --headed
```

Expected outputs:

```text
trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl
trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

The Execution Log records run, login, wave, planned-step, failure, skip, and interrupt events. The Object Registry records SAP object keys observed during execution.

## 5. Download Raw SAP Exports

Probe WebGUI / SE16 access when needed:

```bash
uv run --project post_processor erp-sap-export probe \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env
```

WIN
```bash
uv run --project post_processor erp-sap-export probe --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --env-file configuration/.env
```

Download raw SAP exports:

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

WIN
```bash
uv run --project post_processor erp-sap-export download --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl --env-file configuration/.env --out-dir post_processor/downloads/RUN_EXAMPLE --user-from LEARN-800 --user-to LEARN-899 --window-padding-min 30 --max-keys-per-batch 20 --max-runtime-min 60
```

Expected outputs:

```text
post_processor/downloads/RUN_EXAMPLE/<TABLE>.csv
post_processor/downloads/RUN_EXAMPLE/export-report.json
post_processor/downloads/RUN_EXAMPLE/row-linkage.csv
```

Raw downloads use physical SAP write time for extraction windows. Planned Synthetic Time is applied later during processing.

## 6. Process the Dataset

Run:

```bash
uv run --project post_processor erp-sap-export process \
  --raw-dir post_processor/downloads/RUN_EXAMPLE \
  --out-dir post_processor/processed/RUN_EXAMPLE \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml \
  --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl \
  --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

WIN

```bash
uv run --project post_processor erp-sap-export process --raw-dir post_processor/downloads/RUN_EXAMPLE --out-dir post_processor/processed/RUN_EXAMPLE --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

Processing:

- excludes Failed Process Cases using Execution Evidence and Object Registry
- joins SAP export rows to Process Cases through SAP object keys
- applies Synthetic Timestamp Projection
- projects synthetic actor identity
- preserves technical SAP user identity in provenance
- keeps planned business dates distinct from technical timestamp fields

Expected outputs:

```text
post_processor/processed/RUN_EXAMPLE/<TABLE>.csv
post_processor/processed/RUN_EXAMPLE/provenance.csv
post_processor/processed/RUN_EXAMPLE/processing-report.json
```

## 7. Validate Processed Output

Run:

```bash
uv run --project post_processor erp-sap-export validate-processed \
  --processed-dir post_processor/processed/RUN_EXAMPLE \
  --raw-dir post_processor/downloads/RUN_EXAMPLE \
  --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml \
  --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl \
  --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

WIN
```bash
uv run --project post_processor erp-sap-export validate-processed --processed-dir post_processor/processed/RUN_EXAMPLE --raw-dir post_processor/downloads/RUN_EXAMPLE --execution-trace trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml --post-processing-manifest trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml --execution-log trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl --object-registry trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl
```

A clean validation report has no errors. Investigate warnings before using the dataset for downstream analysis.

## Output Checklist

At the end of a successful run, keep these artifacts together:

- `configuration/build/main.yaml`
- `trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml`
- `trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.post-processing-manifest.yaml`
- `trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-log.jsonl`
- `trace_executor/build/RUN_EXAMPLE/RUN_EXAMPLE.object-registry.jsonl`
- `post_processor/downloads/RUN_EXAMPLE/`
- `post_processor/processed/RUN_EXAMPLE/`

The processed folder is the final user-facing dataset output. The planned artifacts and execution evidence are required when validating or explaining it.
