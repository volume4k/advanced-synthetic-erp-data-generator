# Recording Tools With Playwright

Use Playwright recording as a draft generator for SAP browser flows. Do not commit raw recordings. Convert them into small, reviewed tools.

## Record A Flow

Run Codegen from the generator project:

```bash
uv run --project generator playwright codegen --target=python --output /tmp/recorded_flow.py "https://your-sap-host.example/path?client=XXX&lang=YY"
```

This opens a browser and the Playwright Inspector. Log in, perform the SAP action, stop recording, and inspect `/tmp/recorded_flow.py`.

Keep raw recordings outside the repo, such as `/tmp/recorded_flow.py`. They may contain brittle selectors, accidental credentials, or workflow noise.

## Optional VS Code Workflow

The official Playwright VS Code extension can record actions, pick locators, show the browser, and open traces. It is useful when you want an interactive selector picker while editing code.

Use it as a helper, not as the final source of truth. Generated code still needs cleanup before it becomes a repo tool.

## Convert Recording To A Tool

1. Copy only the meaningful browser actions into a page helper in `src/erp_trace_executor/tools/fiori/pages.py`.
2. Add a small input model and runner module under `src/erp_trace_executor/tools/fiori/`.
3. Register the `ToolSpec` in `src/erp_trace_executor/registry.py`.
4. Add an example trace under `generator/examples/` with no passwords.
5. Run a headed smoke test:

```bash
uv run --project generator erp-trace-exec generator/examples/<trace>.trace.jsonl --headed
```

## Clean Generated Code

Generated recordings often include noise. Remove:

- repeated waits that do not prove success
- full SAP generated IDs when a label, role, or stable partial ID works
- clicks on intermediate UI state that are not required
- direct credentials or local-only values
- assertions that belong to manual smoke notes, not tool output

Keep:

- one navigation path
- one clear action flow
- one clear success wait
- business output extraction, such as a purchase requisition number

## Debug A Recorded Tool

Use headed mode first. If a flow is flaky, add temporary tracing locally:

```python
context.tracing.start(screenshots=True, snapshots=True, sources=True)
# run the flow
context.tracing.stop(path="/tmp/tool-trace.zip")
```

Open it with:

```bash
uv run --project generator playwright show-trace /tmp/tool-trace.zip
```

Do not commit trace archives. They may contain business data.
