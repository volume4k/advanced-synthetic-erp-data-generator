# Adding Tools

This guide is for external contributors adding Playwright-backed tools to the ERP trace executor.

Start with `recording-tools.md` if you are converting a manually recorded SAP flow. Use `locator-guidelines.md` before committing selectors.

## What A Tool Is

A tool is one executable browser action referenced from a canonical execution-trace node:

```yaml
node_id: C001_A1
case_id: C001
tool_name: fiori.create_purchase_requisition
session_id: buyer-session
virtual_actor_id: buyer-a
inputs:
  material: PUMP1902
  quantity: 20
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
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec


class MyToolInput(BaseModel):
    quantity: int = Field(gt=0)


def run_my_tool(context: ExecutionContext, params: MyToolInput) -> ToolResult:
    page = context.get_fiori_page()
    # Call a page helper here.
    return ToolResult(
        task_id=context.record.task_id,
        session_id=context.record.session_id,
        tool=context.record.tool,
        data={
            "status": "done",
            "returned_objects": [
                returned_object("purchase_order", po_number="4500008732")
            ],
        },
    )


MY_TOOL = ToolSpec(
    name="fiori.my_tool",
    input_model=MyToolInput,
    run=run_my_tool,
)
```

Register it in `src/erp_trace_executor/registry.py` so traces can call it.

## Recorded Tool Workflow

Recorded code is draft material. Use it to discover the flow, then rewrite it into the tool shape above.

Minimum contribution:

- input model
- runner function
- `ToolSpec`
- registry entry
- password-free canonical example trace
- manual smoke notes with the command used and observed success value

Recommended contribution:

- focused regression test only when the tool contains reusable pure logic
- repeated live smoke confidence for unstable or high-impact SAP flows

Mark newly recorded SAP tools as experimental in the PR description until they have repeated live smoke confidence.

## Browser Flow Rules

- Use `context.get_fiori_page()` for SAP tool flows. It returns a Playwright-style wrapper that waits after clicks, double-clicks, `Enter`, and `Tab`.
- Prefer accessible selectors: `get_by_label`, `get_by_role`, `get_by_text`.
- Use stable SAP IDs only when labels are not enough.
- Wait for a visible success element before returning.
- Return only useful structured data. Never return passwords or full credential payloads.
- For created SAP objects, use `returned_object(object_type, **keys)` inside `returned_objects`.
- Only return keys observed from SAP or guaranteed by the SAP response. Do not hard-code inferred item keys such as `00010`.
- Avoid live SAP writes in automated tests.

## Fiori Page Wrapper

`context.get_fiori_page()` keeps tool code close to recorded Playwright code while adding Fiori-safe waiting behavior. Normal locator calls still look familiar:

```python
page = context.get_fiori_page()
page.get_by_role("button", name="Position anlegen", exact=True).click()
page.get_by_role("textbox", name="Material").wait_for(state="visible")
```

For safe open/navigation clicks, use `retry_on_next_wait=True`:

```python
page.get_by_role("button", name="Position anlegen", exact=True).click(retry_on_next_wait=True)
page.get_by_role("textbox", name="Material").wait_for(state="visible")
```

That tells the wrapper: if the next explicit `wait_for()` cannot find its target within three seconds, replay the previous click once, then continue the normal wait. Use this only for idempotent UI-opening actions, such as opening a section or app form. Do not use it for `Sichern`, `Bestellen`, submit, approve, or any action that creates or changes business data.

Use explicit business waits after important steps. The wrapper helps with Fiori timing, but a tool should still wait for proof that the next screen or business value exists.

## Testing

Default pytest proves the generator framework, not the live SAP workflow behind every tool. The mandatory test gate is core-centric:

- trace parsing and validation
- executor ordering, state resolution, result capture, and session behavior
- registry and generic `ToolSpec` contracts
- `FioriPage` wait/retry/message-recovery behavior
- `FioriMessageHandler` policy, capture, dismiss, and de-dupe behavior
- CLI, credentials, and configuration boundaries

SAP tools are browser scripts against a live, changing UI. Do not add or update full fake SAP click-flow tests just because a tool gained a new locator, popup, or field. New business tools are covered by the generic tool contract suite as long as they are registered and have at least one valid password-free example trace.

Tool-specific tests are optional and should be reserved for:

- reusable pure helpers such as parsers and formatters
- regressions where a small focused fake is clearer than a live smoke note
- framework behavior that belongs in `FioriPage`, `FioriMessageHandler`, or the executor rather than one tool

Run:

```bash
uv run --project generator pytest generator/tests -q
```

## Fixtures

The fake Fiori app lives in `tests/fixtures/fake_fiori/index.html`. Extend it when you need a deterministic browser flow for automated tests.

Fixture behavior should prove executor or browser-wrapper behavior, not perfectly clone SAP and not mirror every business tool.

## Manual And Live SAP Smoke Tests

For real SAP UI flows:

1. Put credentials in `configuration/.env`.
2. Keep trace files password-free.
3. Run with `--headed`.
4. Verify browser success state and CLI result.
5. Record the command, observed object ID or status, and any relevant screenshot in the PR notes.

Example:

```bash
uv run --project generator erp-trace-exec generator/examples/sap-create-purchase-requisition.execution-trace.yaml --headed
```

Live SAP smoke runs are optional and non-gating unless a PR explicitly opts into them. They should stay separate from the default pytest suite because they require credentials, a healthy SAP tenant, and mutable business data. If an automated live smoke test is added later, mark it with `@pytest.mark.live_sap`; the default pytest config excludes that marker.

Do not commit real credentials, screenshots with secrets, or traces containing passwords.
