# ERP Trace Executor

`trace_executor/` is an independent `uv` project that executes canonical `execution-trace.yaml` files against SAP Fiori through registered Browser Tools. It reuses Playwright browser sessions by explicit Actor Session ids and writes append-only Execution Evidence.

For the end-to-end dataset workflow, start with [the user guide](../docs/user-guide/README.md). This README is the local reference for the executor project, CLI, trace format, and Browser Tool development.

## Component Role

Inputs:

- one canonical **Execution Trace**
- credentials and login URLs resolved from an env file
- registered Browser Tools from `erp_trace_executor.registry`

Outputs:

- `<run_id>.execution-log.jsonl`
- `<run_id>.object-registry.jsonl`
- final JSON result array on stdout

The Trace Executor does not plan Process Cases, schedule waves, sample master data, call the realism LLM, download SAP Exports, or post-process the final Synthetic Dataset.

## Bootstrap

```bash
uv sync --project trace_executor --python 3.13
uv run --project trace_executor playwright install chromium
```

## Run

Run one Execution Trace:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env \
  --artifact-dir trace_executor/build/RUN_EXAMPLE
```

Use headed mode while validating SAP behavior manually:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --env-file configuration/.env \
  --artifact-dir trace_executor/build/RUN_EXAMPLE \
  --headed
```

If `--artifact-dir` is omitted, evidence artifacts are written next to the trace file. For normal dataset runs, pass an explicit directory under `trace_executor/build/<run_id>` so generated traces and execution evidence stay separated.

## CLI Reference

| Argument | Purpose |
|---|---|
| `trace_path` | Path to a canonical Execution Trace YAML file. |
| `--env-file` | Credentials file. Defaults to `configuration/.env`. |
| `--artifact-dir` | Directory for Execution Evidence. Defaults to the trace file directory. |
| `--headed` | Launch Chromium in headed mode. Useful for manual SAP smoke runs and cleanup. |
| `--log-level` | Minimum terminal log level. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`; defaults to `INFO`. |

Exit behavior:

- `0`: execution completed and results were printed.
- `1`: execution failed.
- `130`: execution was interrupted with Ctrl-C.

When a headed execution fails or is interrupted, the browser remains open until the cleanup prompt is acknowledged.

## Logging And Evidence

The executor keeps stdout reserved for the final JSON result array. Runtime progress and diagnostics are logged to stderr through Python's standard `logging` package:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_generator/build/RUN_EXAMPLE/RUN_EXAMPLE.execution-trace.yaml \
  --log-level DEBUG
```

Execution Evidence is the machine-readable runtime source of truth:

- `<run_id>.execution-log.jsonl` records run, login, wave, planned-step, failure, skip, and interrupt events with `severity` and `message` fields.
- `<run_id>.object-registry.jsonl` records SAP object keys captured from structured tool results.

If execution is interrupted with Ctrl-C, the CLI exits with code `130` and writes `*_interrupted` events for the active login or Planned Step plus the run. `SIGKILL` cannot be caught.

## Trace Format

Canonical traces contain Actor Session metadata, Process Cases, dependency graph Planned Steps, Execution Waves, labels, validation metadata, Browser Tool inputs, and required SAP object keys.

Actor Session blocks reference env var names; they do not contain usernames or passwords:

```yaml
actor_sessions:
- actor_session_id: buyer-session
  synthetic_actor_id: buyer-a
  technical_sap_user_id: TU_01
  username_env_var: SAP_USER_1_UN
  password_env_var: SAP_USER_1_PW
  login_url_env_var: SAP_URL
```

The executor logs in every Actor Session before executing waves. Env files provide the actual secrets:

```text
SAP_URL=https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE
SAP_USER_1_UN=BUYER_A
SAP_USER_1_PW=secret
```

Passwords are used only to fill the login form and are not returned in tool results or runtime evidence.

## Returned Objects And Runtime State

Browser Tools return structured `ToolResult` data. Created or observed SAP Business Objects must be returned through `returned_objects`:

```python
returned_object("purchase_order", po_number="4500008732")
```

The executor records these keys in Runtime State and the Object Registry. Later Planned Steps can bind inputs from prior outputs, and the Post-Processor can join SAP Exports back to Process Cases.

## Add A Browser Tool

Detailed guides live in:

- [Adding tools](docs/adding-tools.md)
- [Recording tools with Playwright](docs/recording-tools.md)
- [Locator guidelines](docs/locator-guidelines.md)
- [User guide: Add Browser Tools](../docs/user-guide/add-browser-tools.md)

Short version:

1. Record a draft SAP browser flow with Playwright Codegen.
2. Rewrite the meaningful actions into page helpers under `src/erp_trace_executor/tools/fiori/`.
3. Add a small Pydantic input model and runner.
4. Return structured `returned_objects` for created or observed SAP keys.
5. Register the new `ToolSpec` in `src/erp_trace_executor/registry.py`.
6. Add a password-free canonical example trace under `trace_executor/examples/`.
7. Regenerate configuration with `configuration/create-config.sh`.

Password-free example traces are required for newly registered business tools so generic contract tests can prove the tool is discoverable and has a canonical trace shape. Manually created one-off example traces for local smoke work are optional.

Record live SAP smoke notes in the PR when useful: command, observed non-secret success value, and relevant cleanup notes. Do not commit credentials, trace archives, or screenshots containing secrets.

## Testing

Run default tests:

```bash
uv run --project trace_executor pytest trace_executor/tests -q
```

Default tests are core-centric. They should prove:

- trace parsing and validation
- executor ordering, state resolution, result capture, and Actor Session behavior
- registry and generic `ToolSpec` contracts
- password-free example trace contracts
- `FioriPage` wait, retry, delay, and message-recovery behavior
- `FioriMessageHandler` policy, capture, dismiss, and de-dupe behavior
- CLI, credentials, and configuration boundaries

Do not add or maintain per-tool mocked SAP click-flow tests for normal business tool changes. SAP tools are browser scripts against a live, changing SAP tenant. Tool-specific tests should be reserved for reusable pure helpers, parsers, formatters, or compact regressions.

Automated real SAP checks must be marked `@pytest.mark.live_sap`; default pytest excludes that marker.

## Manual And Live SAP Smoke Tests

For real SAP UI flows:

1. Put credentials in `configuration/.env`.
2. Keep trace files password-free.
3. Run with `--headed`.
4. Verify browser success state and CLI result.
5. Record the command, observed object ID or status, and non-secret notes in the PR.

Example:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_executor/examples/sap-create-purchase-requisition.execution-trace.yaml \
  --env-file configuration/.env \
  --headed
```

Live SAP smoke runs are optional and non-gating unless a PR explicitly opts into them.

## Related Documentation

- [User guide](../docs/user-guide/README.md)
- [Create a dataset](../docs/user-guide/create-dataset.md)
- [Configuration reference](../configuration/README.md)
- [Trace Generator reference](../trace_generator/README.md)
- [Post-Processor reference](../post_processor/README.md)
