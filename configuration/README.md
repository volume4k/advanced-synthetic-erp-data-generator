# Configuration

`configuration/` owns the repository's trace-planning configuration. It describes synthetic actors, technical SAP users, process definitions, Browser Tool wiring, configured master data, scenario controls, realism settings, and run settings.

The **Trace Generator** consumes the compiled YAML produced from this folder. The **Trace Executor** stays execution-only: it reads an Execution Trace and runs Browser Tools against SAP Fiori.

For the end-to-end dataset workflow, start with [the user guide](../docs/user-guide/README.md). This README is the local reference for editing configuration.

## Component Role

Inputs:

- Hand-written Pkl modules in `configuration/*.pkl`.
- Browser Tool metadata generated from the Trace Executor registry.
- Runtime values from `configuration/.env`, especially SAP credentials and realism endpoint settings.

Output:

- `configuration/build/main.yaml`, the compiled configuration consumed by `erp-trace-generate`.

`configuration/create-config.sh` is the supported entrypoint. It regenerates tool metadata, formats and validates Pkl modules, and writes the compiled YAML.

## Current Flow

1. `generate_tool_config.py` reads registered Browser Tools from the Trace Executor registry.
2. It writes `generated_tool_config.pkl` with raw tool facts only.
3. Hand-written Pkl modules define actors, technical users, mappings, master data, processes, scenario controls, and run settings.
4. `main.pkl` imports every module and exposes one complete configuration object.
5. `create-config.sh` validates Pkl and writes YAML.

## File Roles

| File | Purpose |
|---|---|
| `objects.pkl` | Shared class definitions used by all configuration modules. |
| `generated_tool_config.pkl` | Generated Browser Tool catalogue. Do not edit manually. |
| `actors.pkl` | Synthetic actors, capabilities, realism profiles, and final dataset identity projection names. |
| `technical_users.pkl` | Technical SAP user references. Contains env var names only, no secrets. |
| `identity_mapping.pkl` | Mapping from synthetic actors to technical SAP users. |
| `master_data.pkl` | Material/vendor/plant/storage-location matrix and hard sampling guardrails. |
| `processes.pkl` | Process steps, tool assignments, input bindings, required SAP object keys, planned date inputs, and process dependencies. |
| `fraud_scenarios.pkl` | Enabled routine and anomalous scenario controls, target shares, and configured vendor bank-account values. |
| `run_settings.pkl` | Case count, concurrency, timezone, active process types, scheduler seed, working hours, pause ranges, inter-step delays, storage-location labels, realism settings, and post-processing export groups. |
| `main.pkl` | Final public entrypoint for compiled config. |
| `create-config.sh` | Regenerates tool facts, validates Pkl, and writes YAML. |

## What To Edit

| Goal | Edit |
|---|---|
| Add or change a synthetic business persona | `actors.pkl` |
| Add a SAP login account reference | `technical_users.pkl` |
| Link a synthetic actor to technical SAP users | `identity_mapping.pkl` |
| Add or constrain materials, vendors, plants, prices, or quantities | `master_data.pkl` |
| Add a process step or Browser Tool input mapping | `processes.pkl` |
| Enable, disable, or tune scenario types | `fraud_scenarios.pkl` |
| Change run size, scheduling, realism, or export groups | `run_settings.pkl` |
| Refresh available Browser Tool metadata | Run `configuration/create-config.sh` |

Configuration changes are not automatically tested by the Trace Generator pytest suite. To validate repository configuration, compile it with `configuration/create-config.sh`, then run `erp-trace-generate` against the resulting `configuration/build/main.yaml`.

## Actors And Technical Users

Add synthetic business users in `actors.pkl`:

```pkl
new objects.SyntheticActor {
  id = "procurement_01"
  displayName = "Dieter Einkauf"
  role = "procurement"
  timezone = "Europe/Berlin"
  workLocation = "HD00"
  personaDescription = "Careful procurement clerk who pauses before save actions."
  delayMultiplier = 1.2
  realismProfile {
    workerType = "relaxed procurement clerk"
    workingHoursDeviation = -2.5
    pauseCharacteristicsIndex = 12
  }
  realismGuardrails {
    delayMultiplierMin = 0.9
    delayMultiplierMax = 1.8
    workdayDeviationHoursMin = -2.5
    workdayDeviationHoursMax = 0.5
    pauseDurationMinutesMin = 35
    pauseDurationMinutesMax = 90
  }
  exposeInFinalDatasetAs = "procurement_01"
  capabilities {
    new objects.ActorCapability {
      processType = "procure_to_pay"
      stepTypes {
        "create_purchase_requisition"
        "create_purchase_order"
      }
    }
  }
}
```

Scheduling uses `capabilities`, not `role`. `role` is descriptive metadata. A person can execute multiple Process Steps by listing multiple `stepTypes`; the Trace Generator still prevents one actor from working on two Planned Steps at once.

Add SAP accounts in `technical_users.pkl` using environment variable names only:

```pkl
new objects.TechnicalUser {
  id = "GBGEN_P01"
  usernameEnvVar = "SAP_USER_1_UN"
  passwordEnvVar = "SAP_USER_1_PW"
  loginUrlEnvVar = "SAP_URL"
}
```

Put the actual values in `configuration/.env`, never in Pkl:

```text
SAP_URL=https://your-sap-host.example/sap/bc/ui2/flp?sap-client=204&sap-language=DE
SAP_USER_1_UN=BUYER_A
SAP_USER_1_PW=secret
```

Connect synthetic actors to technical users in `identity_mapping.pkl`. The mapping must cover every actor that can be scheduled.

## Processes And Input Bindings

Edit `processes.pkl` to assign Browser Tools to Process Steps. Version-one procure-to-pay has no approval/release step in the current SAP environment:

```pkl
new objects.ProcessStep {
  stepId = "A1"
  stepType = "create_purchase_requisition"
  tool = toolRequirements["fiori.create_purchase_requisition"]
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
  requiredSapObjectKeys {
    "purchase_requisition.pr_number"
  }
}
```

Active steps must have:

- a registered Browser Tool
- bindings for every required tool input
- at least one required SAP object key

Bindings are owned by each `ProcessStep`; `run_settings.pkl` must not contain fallback tool-input maps.

Supported binding sources are `literal`, `master_data`, `case`, `planned_date`, `prior_output`, and `derived`. Supported derived values in v1 are `gross_amount`, `fiori_delivery_date`, `fiori_payment_posting_date`, `storage_location_label`, `quality_inspection_quantity`, and `unrestricted_quantity`.

`plannedDateInputBindings` hold planned date inputs for the Execution Trace and Post-Processing Manifest. Use them when SAP runtime either cannot accept the planned date, as with goods receipt, or when post-processing needs a stable planned date contract.

Supplier invoice uses this split deliberately: the Trace Executor fills SAP `Rechnungsdatum` with the executor's current date, while `plannedDateInputBindings.invoice_date` carries the Planned Synthetic Time date for post-processing.

Dependencies define Process Step ordering:

```pkl
new objects.ProcessDependency {
  fromStepType = "create_purchase_requisition"
  toStepType = "create_purchase_order"
  description = "Create purchase order after purchase requisition because A2 needs the purchase requisition number produced by A1."
}
```

This means `create_purchase_requisition` must happen before `create_purchase_order`.

## Realism Settings

Keep trace-planning settings in Pkl. `run_settings.pkl` defines FIFO scheduling, core working hours, pause ranges, deterministic step-duration ranges, inter-step waiting-time ranges, storage-location labels, logical post-processing export groups, and optional realism compiler settings.

When `runSettings.realism.enabled` is `true`, `erp-trace-generate` calls an OpenAI-compatible local LLM endpoint before scheduling. Realism v2 asks the LLM for compact actor baseline models, per-material price anchors, material demand profiles, quantity profiles, and daily demand patterns; the Trace Generator expands exact Process Cases deterministically from those models.

### Endpoint

The client appends `/v1/chat/completions` to `REALISM_LLM_BASE_URL`. Configure the base URL as the prefix before `/v1`; for this project's OpenAI-compatible local endpoint shape, use a base URL ending in `/api`.

```bash
REALISM_LLM_BASE_URL=http://localhost:1234/api
REALISM_LLM_MODEL=<model-name>
REALISM_LLM_API_KEY=<optional-token>
```

That example resolves to `http://localhost:1234/api/v1/chat/completions`. Do not include `/v1` or `/v1/chat/completions` in `REALISM_LLM_BASE_URL`.

The Trace Generator CLI reads these values from `configuration/.env` by default. Shell environment variables take precedence over values in that file.

### Guardrails And Profiles

Demand patterns must sum to `caseCount`. The Trace Generator expands them into demand releases, requested delivery dates, material assignments, order quantities, and anchored prices inside the hard configured master-data guardrails.

Material assignment is controlled by **Material Demand Profiles**, not by daily demand patterns. Each active material must appear once in the LLM response by default. The LLM emits a positive `relative_demand_weight`; the Trace Generator normalizes those weights into exact Process Case counts, shuffles assignments with `schedulerSeed`, and rejects missing, duplicate, or unexpected material IDs. Use `maxMaterialSharePerHorizon` to cap one material's horizon share when a run should force diversity.

Quantity generation is controlled by each material's **Quantity Profile** plus the configured master-data `orderMultiple`. The LLM proposes `typical_order_quantity`, `quantity_variation_pct`, and `bulk_order_share`; it echoes the configured order multiple for context, but the Trace Generator uses the material master value as the source of truth. The Trace Generator samples the final Process Case quantity, rounds to the material `orderMultiple`, and clamps to the hard `quantityMin` and `quantityMax`.

Configure allowed multiples and material locks in `run_settings.pkl`:

```pkl
realism {
  relativeDemandWeightMin = 1
  relativeDemandWeightMax = 100
  quantityVariationPctMin = 0.05
  quantityVariationPctMax = 0.5
  maxBulkOrderShare = 0.35
  allowedOrderMultiples = new Listing {
    1
    5
    10
    20
    25
    50
  }
  maxMaterialSharePerHorizon = 0.35
  requireAllActiveMaterialsInDemandProfile = true
  materialValuationLockEnabled = true
  materialValuationLockBufferSeconds = 120
  blockedMaterials = new Listing {}
}
```

`materialValuationLockEnabled` makes the Trace Generator treat `(plant, material_id)` as a lock resource for goods receipt and supplier invoice posting. Those lock-sensitive steps cannot share one Execution Wave for the same key, and `materialValuationLockBufferSeconds` separates them in Planned Synthetic Time. `blockedMaterials` excludes currently externally locked materials from realism compilation and Process Case planning before an Execution Trace is written.

The Trace Executor does not know material demand profiles, normalize weights, sample quantities, or do scheduling math. It only receives the final Execution Trace fields such as material, vendor, quantity, target price, requested delivery date, Planned Synthetic Time, and human delay profile.

### Cache

Validated compiler output is cached in `configuration/build/realism-criteria.<hash>.json`. The cache is only a performance optimization; generated Execution Traces and Post-Processing Manifests contain the runtime and post-processing fields they need.

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

## Validation Checklist

After configuration edits, run:

```bash
configuration/create-config.sh
uv run --project trace_generator erp-trace-generate \
  configuration/build/main.yaml \
  --out-dir trace_generator/build/RUN_EXAMPLE \
  --run-id RUN_EXAMPLE
```

Use `--env-file path/to/.env` on `erp-trace-generate` when the run should not read `configuration/.env`.

## Related Documentation

- [User guide](../docs/user-guide/README.md)
- [Create a dataset](../docs/user-guide/create-dataset.md)
- [Add Browser Tools](../docs/user-guide/add-browser-tools.md)
- [Trace Generator reference](../trace_generator/README.md)
- [Trace Executor reference](../trace_executor/README.md)
- [Post-Processor reference](../post_processor/README.md)
