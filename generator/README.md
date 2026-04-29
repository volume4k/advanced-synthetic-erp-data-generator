# ERP Trace Executor

`generator/` is an independent `uv` project inside the repository. It executes JSONL traces sequentially and reuses Playwright browser sessions by explicit `session_id`.

## Bootstrap

```bash
uv sync --project generator --python 3.13
uv run --project generator playwright install chromium
```

## Run

```bash
uv run --project generator erp-trace-exec path/to/trace.jsonl
```

The CLI prints one JSON object per executed task result as a JSON array.

Credentials can be loaded from `configuration/.env` by default, or from another file with `--env-file`:

```bash
uv run --project generator erp-trace-exec path/to/trace.jsonl --env-file path/to/credentials.env
```

## Trace Format

Task JSONL lines must contain:

```json
{
  "task_id": "task-001",
  "session_id": "session-001",
  "user_id": "buyer-a",
  "tool": "fiori.login",
  "input": {
    "username": "buyer-a",
    "password": "secret"
  },
  "meta": {
    "case_id": "case-1"
  }
}
```

`session_id` is the only key used for browser-session reuse. Reusing a `session_id` with a different `user_id` is an error.

### Optional Init Login Record

A trace can start with one `kind: "init"` record. The executor logs in each configured user before running task records. Later tasks reuse the initialized `session_id` and `user_id`, so username and password do not need to be repeated.

```json
{"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"BUYERA","login_url":"https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"},{"session_id":"approver-session","user_id":"approver-a","username":"APPROVERA"}]}
{"task_id":"task-001","session_id":"buyer-session","user_id":"buyer-a","tool":"fiori.create_order","input":{"item_name":"widget","quantity":3}}
{"task_id":"task-002","session_id":"approver-session","user_id":"approver-a","tool":"fiori.create_order","input":{"item_name":"gadget","quantity":1}}
```

When an init user omits `password`, the executor looks up that password by username in the env file:

```text
SAP_USER_1_UN=BUYERA
SAP_USER_1_PW=secret
SAP_USER_2_UN=APPROVERA
SAP_USER_2_PW=secret
```

`login_url` is optional and defaults to:

```text
https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE
```

For non-SAP fixtures or custom logon forms, each init user and `fiori.login` task can override selectors:

```json
{
  "username_selector": "[data-testid=\"username\"]",
  "password_selector": "[data-testid=\"password\"]",
  "submit_selector": "[data-testid=\"login-submit\"]",
  "success_selector": "[data-testid=\"session-user\"]"
}
```

Passwords are used only to fill the login form and are not returned in tool results.

## Add A New Playwright Tool

See these contributor guides:

- `docs/adding-tools.md`
- `docs/recording-tools.md`
- `docs/locator-guidelines.md`

1. Add a small input model and runner under `src/erp_trace_executor/tools/`.
2. Keep page selectors and flows in a page-object helper instead of inside the tool module.
3. Register the new `ToolSpec` in `src/erp_trace_executor/registry.py`.

Use the existing `fiori.login` and `fiori.create_order` tools as the reference shape:

- validate `input` with a `pydantic` model
- obtain the browser session from `ExecutionContext`
- call page-object methods
- return a structured `ToolResult`
