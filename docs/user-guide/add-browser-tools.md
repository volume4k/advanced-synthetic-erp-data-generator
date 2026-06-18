# Add Browser Tools

A **Browser Tool** is a deterministic SAP Fiori browser operation assigned to a configured **Process Step**. Add a new Browser Tool when the dataset needs a SAP action that is not yet registered in the Trace Executor.

This guide covers the full path from recording a SAP flow to exposing it through configuration.

## 1. Record a Draft Flow

Use Playwright Codegen from the Trace Executor project:

```bash
uv run --project trace_executor playwright codegen \
  --target=python \
  --output /tmp/recorded_flow.py \
  "https://your-sap-host.example/path?client=XXX&lang=YY"
```

Log in, perform the SAP action once, stop recording, and inspect `/tmp/recorded_flow.py`.

Keep raw recordings outside the repository. They may contain brittle selectors, local-only values, credentials, or business data.

## 2. Convert the Recording into a Tool

Create a focused tool module under `trace_executor/src/erp_trace_executor/tools/fiori/`. Most tools follow this shape:

```python
from pydantic import BaseModel, Field

from erp_trace_executor.context import ExecutionContext
from erp_trace_executor.models import ToolResult, returned_object
from erp_trace_executor.tooling import ToolSpec


class MyToolInput(BaseModel):
    material: str = Field(min_length=1)
    quantity: int = Field(gt=0)


def run_my_tool(context: ExecutionContext, params: MyToolInput) -> ToolResult:
    page = context.get_fiori_page()
    # Call page helpers or compact flow helpers here.
    return ToolResult(
        planned_step_id=context.record.planned_step_id,
        actor_session_id=context.record.actor_session_id,
        tool=context.record.tool,
        data={
            "status": "created",
            "returned_objects": [
                returned_object("my_object_type", object_number="1234567890")
            ],
        },
    )


MY_TOOL = ToolSpec(
    name="fiori.my_tool",
    input_model=MyToolInput,
    run=run_my_tool,
)
```

Use `context.get_fiori_page()` for SAP Fiori flows. Prefer role, label, and text locators before generated SAP IDs. Wait for a visible success condition before returning, and return only observed SAP object keys.

Do not return passwords, full credential payloads, or inferred SAP keys that the UI did not expose.

## 3. Register the Tool

Import and register the `ToolSpec` in `trace_executor/src/erp_trace_executor/registry.py`:

```python
from erp_trace_executor.tools.fiori.my_tool import MY_TOOL


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(MY_TOOL)
    return registry
```

The configuration generator reads this registry and extracts the tool name and Pydantic input schema.

## 4. Optionally Add a Password-Free Example Trace

An example trace is optional. Add one under `trace_executor/examples/` when it helps reviewers or future contributors run a small smoke check without generating a full dataset first. The example should prove the canonical trace shape for the new tool without containing credentials.

For a single-step smoke trace, keep the file intentionally small:

- one `actor_sessions` entry using environment variable names
- one `cases` entry
- one `dependency_graph.planned_steps` entry for the new Browser Tool
- `dependencies: []`
- one `execution_schedule.waves` entry that contains only that planned step

Use this skeleton and replace the tool name, inputs, and returned object key contract with the new tool's values:

```yaml
trace_version: "0.3"
run_id: RUN_EXAMPLE_MY_TOOL
config_hash: example
tool_catalog_hash: example
trace_generator_version: 0.1.0
llm_metadata:
  used: false
  seed: 1
actor_sessions:
- actor_session_id: example-session
  synthetic_actor_id: example_actor
  technical_sap_user_id: TU_01
  username_env_var: SAP_USER_1_UN
  password_env_var: SAP_USER_1_PW
  login_url_env_var: SAP_URL
  success_selector: "#userActionsMenuHeaderButton"
cases:
- case_id: EXAMPLE_MY_TOOL_001
  process_type: procure_to_pay
  case_scenario_type: NORMAL
  line_items: []
dependency_graph:
  planned_steps:
  - planned_step_id: planned-step-my-tool-001
    case_id: EXAMPLE_MY_TOOL_001
    step_type: my_new_step
    tool_name: fiori.my_tool
    synthetic_actor_id: example_actor
    technical_sap_user_id: TU_01
    actor_session_id: example-session
    inputs:
      material: PUMP1902
      quantity: 10
    required_sap_object_keys:
    - my_object_type.object_number
    planned_date_inputs: {}
    planned_synthetic_time:
      start: "2026-05-18T08:00:00+02:00"
      end: "2026-05-18T08:10:00+02:00"
    labels:
      step_label: smoke
  dependencies: []
execution_schedule:
  mode: waves
  max_parallel_actor_sessions: 1
  waves:
  - wave_id: W001
    sequence_no: 1
    planned_steps:
    - planned_step_id: planned-step-my-tool-001
      startup_order: 1
validation_report:
  errors: []
  warnings: []
```

If the tool requires a value from a prior SAP object, a single-step trace is not enough unless you hard-code a safe existing SAP object key in `inputs`. Use that only for local smoke testing and keep committed examples password-free and free of sensitive business data.

Run a headed smoke test when SAP access is available:

```bash
uv run --project trace_executor erp-trace-exec \
  trace_executor/examples/sap-my-tool.execution-trace.yaml \
  --env-file configuration/.env \
  --headed
```

Record the command, observed success value, and relevant non-secret notes in the pull request.

## 5. Regenerate Tool Configuration

Run:

```bash
configuration/create-config.sh
```

This updates `configuration/generated_tool_config.pkl` from the registered Browser Tools and then writes `configuration/build/main.yaml`.

Do not edit `configuration/generated_tool_config.pkl` by hand.

## 6. Wire the Tool into Process Configuration

In `configuration/processes.pkl`, assign the registered tool to a `ProcessStep`:

```pkl
myStep: objects.ProcessStep = new objects.ProcessStep {
  stepId = "X1"
  stepType = "my_new_step"
  tool = toolRequirements["fiori.my_tool"]
  inputBindings {
    new objects.ToolInputBinding {
      field = "material"
      source = "master_data"
      value = "materialId"
    }
    new objects.ToolInputBinding {
      field = "quantity"
      source = "case"
      value = "quantity"
      valueType = "int"
    }
  }
  requiredSapObjectKeys {
    "my_object_type.object_number"
  }
  labels {
    ["step_label"] = "routine_step"
  }
}
```

Choose `InputBinding.source` deliberately:

- `literal`: a fixed configured value
- `master_data`: a field from Configured Master Data
- `case`: a field sampled for the Process Case
- `planned_date`: a planned date value
- `prior_output`: a SAP object key returned by an earlier Planned Step
- `derived`: a supported derived value computed by the Trace Generator
- `vendor_bank_account`: scenario-controlled vendor bank-account values

Add `plannedDateInputBindings` when a planned date must appear in the Execution Trace or Post-Processing Manifest, especially when SAP runtime cannot accept that date directly.

Add `requiredSapObjectKeys` for each SAP object key downstream steps or post-processing need. The Browser Tool must return those keys as structured `returned_objects`.

## 7. Update Scheduling and Data Model Configuration

After adding the Process Step, update related configuration as needed:

- `actors.pkl`: add the new `stepType` to each Synthetic Actor capability that may perform it.
- `run_settings.pkl`: add `stepDurationMinutes` and `interStepDelayMinutes` entries for scheduling.
- `master_data.pkl`: add or adjust Configured Master Data fields if the tool needs new material, vendor, plant, storage-location, quantity, or price constraints.
- `processes.pkl`: add `ProcessDependency` entries so the new step runs after the SAP object keys it needs are available.
- `fraud_scenarios.pkl`: add scenario-specific values or labels if the step represents a Case Scenario Type.
- `run_settings.pkl`: add `postProcessingExportGroups` when the new SAP objects require additional export groups.

Re-run `configuration/create-config.sh` after each configuration change.

## 8. Validate the Integration

Run the default Trace Executor tests:

```bash
uv run --project trace_executor pytest trace_executor/tests -q
```

Run the Trace Generator tests when the tool changes configuration contracts:

```bash
uv run --project trace_generator pytest trace_generator/tests -q
```

Run a small generated trace through the full workflow before using the new tool in a large dataset.

Default automated tests should stay core-centric. Do not add fake SAP click-flow tests for normal business tool changes. Add tool-specific tests only for reusable pure helpers, parsers, formatters, or compact regressions. Mark automated real SAP checks with `@pytest.mark.live_sap` so they stay out of the default pytest gate.

## Existing References

- [Trace Executor adding-tools reference](../../trace_executor/docs/adding-tools.md)
- [Recording tools with Playwright](../../trace_executor/docs/recording-tools.md)
- [Locator guidelines](../../trace_executor/docs/locator-guidelines.md)
