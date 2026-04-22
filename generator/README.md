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

## Trace Format

Each JSONL line must contain:

```json
{
  "task_id": "task-001",
  "session_id": "session-001",
  "user_id": "buyer-a",
  "tool": "fiori.login",
  "input": {
    "base_url": "http://127.0.0.1:8000",
    "username": "buyer-a",
    "password": "secret"
  },
  "meta": {
    "case_id": "case-1"
  }
}
```

`session_id` is the only key used for browser-session reuse. Reusing a `session_id` with a different `user_id` is an error.

## Add A New Playwright Tool

1. Add a small input model and runner under `src/erp_trace_executor/tools/`.
2. Keep page selectors and flows in a page-object helper instead of inside the tool module.
3. Register the new `ToolSpec` in `src/erp_trace_executor/registry.py`.

Use the existing `fiori.login` and `fiori.create_order` tools as the reference shape:

- validate `input` with a `pydantic` model
- obtain the browser session from `ExecutionContext`
- call page-object methods
- return a structured `ToolResult`
