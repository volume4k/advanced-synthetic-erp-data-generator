# Adding Tools

This guide is for external contributors adding Playwright-backed tools to the ERP trace executor.

## What A Tool Is

A tool is one executable browser action referenced from a JSONL trace record:

```json
{"task_id":"task-001","session_id":"session-001","user_id":"buyer-a","tool":"fiori.create_purchase_requisition","input":{"material":"PUMP1902","quantity":20}}
```

The executor validates `input`, reuses the browser session identified by `session_id`, runs the tool, and prints a structured result.

## File Pattern

Add most tools under `src/erp_trace_executor/tools/fiori/`:

```text
create_purchase_requisition.py  # input model, runner, ToolSpec
pages.py                        # browser selectors and page flows
```

Keep executor logic out of tool modules. Keep browser selectors and multi-step UI flows in page helpers.

## Tool Module Shape

Use this shape:

```python
from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult
from erp_trace_executor.tooling import ToolSpec


class MyToolInput(BaseModel):
    quantity: int = Field(gt=0)


def run_my_tool(context: ExecutionContext, params: MyToolInput) -> ToolResult:
    session = context.get_browser_session()
    page = session.page
    # Call a page helper here.
    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={"status": "done"},
    )


MY_TOOL = ToolSpec(
    name="fiori.my_tool",
    input_model=MyToolInput,
    run=run_my_tool,
)
```

Register it in `src/erp_trace_executor/registry.py` so traces can call it.

## Browser Flow Rules

- Prefer accessible selectors: `get_by_label`, `get_by_role`, `get_by_text`.
- Use stable SAP IDs only when labels are not enough.
- Wait for a visible success element before returning.
- Return only useful structured data. Never return passwords or full credential payloads.
- Avoid live SAP writes in automated tests.

## Testing

Each new tool should have:

- input validation tests for required fields and bounds
- registry test that default registry exposes the tool
- fixture integration test that logs in, runs the tool, and checks result data
- example trace parse test if you add an example JSONL file

Run:

```bash
uv run --project generator pytest generator/tests -q
```

## Fixtures

The fake Fiori app lives in `tests/fixtures/fake_fiori/index.html`. Extend it when you need a deterministic browser flow for automated tests.

Fixture behavior should prove executor/tool integration, not perfectly clone SAP.

## Manual SAP Smoke Test

For real SAP UI flows:

1. Put credentials in `configuration/.env`.
2. Keep trace files password-free.
3. Run with `--headed`.
4. Verify browser success state and CLI result.

Example:

```bash
uv run --project generator erp-trace-exec generator/examples/sap-create-purchase-requisition.trace.jsonl --headed
```

Do not commit real credentials, screenshots with secrets, or traces containing passwords.
