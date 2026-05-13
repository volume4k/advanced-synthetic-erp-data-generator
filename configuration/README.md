# Configuration

This folder owns trace-planning configuration. The generator stays execution-only: it reads a trace and runs browser tools.

## Current Flow

1. `generate_tool_config.py` reads registered generator tools.
2. It writes `generated_tool_config.pkl` with raw tool facts only.
3. Hand-written Pkl modules define actors, technical users, mappings, master data, processes, fraud placeholders, and run settings.
4. `main.pkl` imports every module and exposes one complete configuration object.
5. `create-config.sh` validates Pkl and writes YAML.

## File Roles

- `objects.pkl`: shared class definitions.
- `generated_tool_config.pkl`: generated raw tool catalogue. Do not edit manually.
- `actors.pkl`: virtual actors and realism profiles.
- `technical_users.pkl`: SAP technical user references. Contains env var names only, no secrets.
- `identity_mapping.pkl`: mapping from virtual actors to technical SAP users.
- `master_data.pkl`: material/vendor/plant/storage-location matrix and sampling ranges.
- `processes.pkl`: process steps, tool assignments, step-local input bindings, expected outputs, and process dependencies.
- `fraud_scenarios.pkl`: enabled fraud scenario placeholders and target shares.
- `run_settings.pkl`: case count, concurrency, timezone, active process types, scheduler seed, working hours, pause ranges, inter-step delay ranges, storage-location labels, and post-processing export groups.
- `main.pkl`: final public entrypoint for compiled config.
- `create-config.sh`: regenerates tool facts, validates Pkl, writes YAML.

## Actors And Technical Users

Add synthetic business users in `actors.pkl`:

```pkl
new objects.VirtualActor {
  id = "procurement_01"
  displayName = "Dieter Einkauf"
  role = "procurement"
  timezone = "Europe/Berlin"
  workLocation = "HD00"
  speedFactor = 1.2
  realismProfile {
    workerType = "relaxed procurement clerk"
    workingHoursDeviation = -2.5
    pauseCharacteristicsIndex = 12
  }
  exposeInFinalDatasetAs = "procurement_01"
}
```

Add SAP accounts in `technical_users.pkl` using environment variable names only:

```pkl
new objects.TechnicalUser {
  id = "GBGEN_P01"
  usernameEnvVar = "SAP_USER_1_UN"
  passwordEnvVar = "SAP_USER_1_PW"
  loginUrlEnvVar = "SAP_URL"
}
```

Connect both in `identity_mapping.pkl`.

## Processes

Edit `processes.pkl` to assign tools to process steps. Version-one procure-to-pay has no approval/release step in the current SAP environment:

```pkl
new objects.ProcessStep {
  stepId = "A1"
  stepType = "create_purchase_requisition"
  tool = toolRequirements["fiori.create_purchase_requisition"]
  requiredRole = "procurement"
  inputBindings {
    new objects.ToolInputBinding {
      field = "material"
      source = "master_data"
      value = "materialId"
    }
    new objects.ToolInputBinding {
      field = "delivery_date"
      source = "derived"
      value = "fiori_delivery_date"
    }
  }
  expectedOutputs {
    "purchase_requisition.pr_number"
  }
}
```

Active steps must have a tool, bindings for every required tool input, and at least one expected output key. Bindings are owned by each `ProcessStep`; `run_settings.pkl` must not contain fallback tool-input maps.

Supported binding sources are `literal`, `master_data`, `case`, `business_date`, `prior_output`, and `derived`. Supported derived values in v1 are `gross_amount`, `fiori_delivery_date`, `fiori_payment_posting_date`, and `storage_location_label`.

Dependencies define directed graph edges:

```pkl
new objects.ProcessDependency {
  fromStepType = "create_purchase_requisition"
  toStepType = "create_purchase_order"
  description = "Create purchase order after purchase requisition because A2 needs the purchase requisition number produced by A1."
}
```

This means `create_purchase_requisition` must happen before `create_purchase_order`.

## Trace Generator Settings

Keep trace-planning settings in Pkl. `run_settings.pkl` defines FIFO scheduling, core working hours, pause ranges, deterministic step-duration ranges, inter-step waiting-time ranges, storage-location labels, and logical post-processing export groups. Those ranges are sampled by the trace generator today; an LLM can generate or refine the ranges later, but the compiled YAML remains the structured source of truth.

## Build YAML

Run:

```bash
configuration/create-config.sh
```

This regenerates `generated_tool_config.pkl`, validates all Pkl modules, and writes:

- `configuration/build/main.yaml`

Custom output:

```bash
configuration/create-config.sh /tmp/main.yaml
```
