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
- `actors.pkl`: synthetic actors and realism profiles.
- `technical_users.pkl`: SAP technical user references. Contains env var names only, no secrets.
- `identity_mapping.pkl`: mapping from synthetic actors to technical SAP users.
- `master_data.pkl`: material/vendor/plant/storage-location matrix and hard sampling guardrails.
- `processes.pkl`: process steps, tool assignments, step-local input bindings, required SAP object keys, and process dependencies.
- `fraud_scenarios.pkl`: enabled fraud scenario placeholders and target shares.
- `run_settings.pkl`: case count, concurrency, timezone, active process types, scheduler seed, working hours, pause ranges, inter-step delay ranges, storage-location labels, and post-processing export groups.
- `main.pkl`: final public entrypoint for compiled config.
- `create-config.sh`: regenerates tool facts, validates Pkl, writes YAML.

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
  runtimeDelayCapSeconds = 4.0
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
    runtimeDelayCapSecondsMin = 2.0
    runtimeDelayCapSecondsMax = 6.0
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

Scheduling uses `capabilities`, not `role`. `role` is descriptive metadata. A person can execute multiple process steps by listing multiple `stepTypes`; the trace generator still prevents one actor from working on two planned steps at once.

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

Active steps must have a tool, bindings for every required tool input, and at least one required SAP object key. Bindings are owned by each `ProcessStep`; `run_settings.pkl` must not contain fallback tool-input maps.

`plannedDateInputBindings` hold planned date inputs for the canonical trace and post-processing manifest. Use them when SAP runtime either cannot accept the planned date, as with goods receipt, or when post-processing needs a stable planned date contract.

Supported binding sources are `literal`, `master_data`, `case`, `planned_date`, `prior_output`, and `derived`. Supported derived values in v1 are `gross_amount`, `fiori_delivery_date`, `fiori_payment_posting_date`, and `storage_location_label`.

Dependencies define process-step ordering:

```pkl
new objects.ProcessDependency {
  fromStepType = "create_purchase_requisition"
  toStepType = "create_purchase_order"
  description = "Create purchase order after purchase requisition because A2 needs the purchase requisition number produced by A1."
}
```

This means `create_purchase_requisition` must happen before `create_purchase_order`.

## Trace Generator Settings

Keep trace-planning settings in Pkl. `run_settings.pkl` defines FIFO scheduling, core working hours, pause ranges, deterministic step-duration ranges, inter-step waiting-time ranges, storage-location labels, logical post-processing export groups, and optional realism compiler settings.

When `runSettings.realism.enabled` is `true`, `erp-trace-generate` calls an OpenAI-compatible local LLM endpoint before scheduling. Realism v2 asks the LLM for compact actor baseline models, per-material price anchors, material demand profiles, quantity profiles, and daily demand patterns; the trace generator expands exact process cases deterministically from those models.

Configure the endpoint with:

```bash
REALISM_LLM_BASE_URL=http://localhost:1234
REALISM_LLM_MODEL=<model-name>
REALISM_LLM_API_KEY=<optional-token>
```

The trace-generator CLI reads these values from `configuration/.env` by default. Shell environment variables take precedence over values in that file.

Validated compiler output is cached in `configuration/build/realism-criteria.<hash>.json`. The cache is only a performance optimization; generated execution traces and manifests contain the runtime and post-processing fields they need.

Demand patterns must sum to `caseCount`. The trace generator expands them into demand releases, requested delivery dates, material assignments, order quantities, and anchored prices inside the hard master-data guardrails.

Material assignment is controlled by **Material Demand Profiles**, not by daily demand patterns. Each active material must appear once in the LLM response by default. The LLM emits a positive `relative_demand_weight`; the trace generator normalizes those weights into exact process-case counts, shuffles assignments with `schedulerSeed`, and rejects missing, duplicate, or unexpected material IDs. Use `maxMaterialSharePerHorizon` to cap one material's horizon share when a run should force diversity.

Quantity generation is controlled by each material's **Quantity Profile**. The LLM proposes `typical_order_quantity`, `quantity_variation_pct`, `bulk_order_share`, and `order_multiple`. The trace generator samples the final process-case quantity, rounds to the order multiple, and clamps to the material's hard `quantityMin` and `quantityMax`. Configure guardrails in `run_settings.pkl`:

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
}
```

The trace executor does not know material demand profiles, normalize weights, sample quantities, or do scheduling math. It only receives the final execution trace fields such as material, vendor, quantity, target price, requested delivery date, planned synthetic time, and human delay profile.

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
