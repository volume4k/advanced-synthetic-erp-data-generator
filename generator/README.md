# ERP Trace Executor

`generator/` is an independent `uv` project inside the repository. It executes canonical `execution-trace.yaml` files and reuses Playwright browser sessions by explicit `session_id`.

## Bootstrap

```bash
uv sync --project generator --python 3.13
uv run --project generator playwright install chromium
```

## Run

```bash
uv run --project generator erp-trace-exec path/to/execution-trace.yaml
```

The CLI prints one JSON object per executed task result as a JSON array.

Canonical execution runs waves sequentially in planned `startup_order` and writes append-only runtime evidence:

- `<run_id>.execution-log.jsonl`
- `<run_id>.object-registry.jsonl`

Credentials can be loaded from `configuration/.env` by default, or from another file with `--env-file`:

```bash
uv run --project generator erp-trace-exec path/to/execution-trace.yaml --env-file path/to/credentials.env --artifact-dir generator/build
```

## Logging

The executor keeps stdout reserved for the final JSON result array. Runtime progress and diagnostics are logged to stderr through Python's standard `logging` package.

```bash
uv run --project generator erp-trace-exec path/to/execution-trace.yaml --log-level DEBUG
```

`--log-level` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` and defaults to `INFO`. Evidence artifacts remain the machine-readable source of truth:

- `<run_id>.execution-log.jsonl` records run, login, wave, planned-step, failure, skip, and interrupt events with `severity` and `message` fields.
- `<run_id>.object-registry.jsonl` records created SAP object keys.

If execution is interrupted with Ctrl-C, the CLI exits with code `130` and writes `*_interrupted` events for the active login or planned step plus the run. `SIGKILL` cannot be caught.

## Trace Format

Canonical traces contain actor session metadata, cases, dependency graph planned steps, waves, and validation metadata. Actor session blocks reference env var names; they do not contain usernames or passwords:

```yaml
actor_sessions:
- actor_session_id: buyer-session
  synthetic_actor_id: buyer-a
  technical_sap_user_id: TU_01
  username_env_var: SAP_USER_1_UN
  password_env_var: SAP_USER_1_PW
  login_url_env_var: SAP_URL
```

The executor logs in every actor session before executing waves. Env files provide the actual secrets:

```text
SAP_URL=https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE
SAP_USER_1_UN=BUYER_A
SAP_USER_1_PW=secret
```

Passwords are used only to fill the login form and are not returned in tool results or runtime evidence.

## Add A New Playwright Tool

See these contributor guides:

- `docs/adding-tools.md`
- `docs/recording-tools.md`
- `docs/locator-guidelines.md`

1. Add a small input model and runner under `src/erp_trace_executor/tools/`.
2. Keep page selectors and flows in a page-object helper instead of inside the tool module.
3. Register the new `ToolSpec` in `src/erp_trace_executor/registry.py`.

Use the existing `fiori.login` and `fiori.create_purchase_requisition` tools as the reference shape:

- validate `input` with a `pydantic` model
- obtain the browser session from `ExecutionContext`
- call page-object methods
- return a structured `ToolResult`
