# Configuration

This folder owns planning configuration. The generator remains execution-only: it reads a trace and runs browser tools.

## Current Flow

1. `generate_tool_config.py` reads the registered generator tools.
2. It writes `generated_tool_config.pkl` with raw tool facts only:
   - tool name;
   - human title;
   - input model name;
   - required input fields;
   - input property schema/default information.
3. `tool_configuration.pkl` imports those raw tool facts and defines process meaning in Pkl.
4. `tool_configuration.pkl` assigns tools to procure-to-pay process steps.
5. `tool_configuration.pkl` defines graph dependencies between steps.

## File Roles

- `generate_tool_config.py`: small extraction script. It must not contain process logic, step ordering, role mapping, or graph dependencies.
- `generated_tool_config.pkl`: generated raw tool catalogue. Do not edit manually.
- `tool_config_schema.pkl`: Pkl classes used by generated and hand-written configuration.
- `tool_configuration.pkl`: hand-written process configuration. Edit this file to assign tools to process steps and define dependencies.

## Process Steps

Each process step has:

```pkl
new tool_config_schema.ProcessStep {
  stepId = "A1"
  stepType = "create_purchase_requisition"
  tool = toolRequirements["fiori.create_purchase_requisition"]
  requiredRole = "procurement"
}
```

- `stepId` is the graph node id. It should be unique inside the process template.
- `stepType` describes what the step does.
- `tool` assigns an available generator tool. Use `null` while the tool does not exist yet.
- `requiredRole` defines the role expected to execute the step.

## Dependencies

Dependencies define directed graph edges between process steps:

```pkl
new tool_config_schema.ProcessDependency {
  fromStepType = "create_purchase_requisition"
  toStepType = "create_purchase_order"
  description = "Create purchase order after purchase requisition because A2 needs the purchase requisition number produced by A1."
}
```

This means `create_purchase_requisition` must happen before `create_purchase_order`.

The dependency list currently describes ordering only. Add richer fields later only if the scheduler needs them.

## Regenerating Tool Facts

Run:

```bash
configuration/create-config.sh
```

This regenerates `generated_tool_config.pkl`, validates the Pkl modules, and writes:

- `configuration/build/tool_configuration.yaml`

To choose another YAML output path:

```bash
configuration/create-config.sh /tmp/tool_configuration.yaml
```
