# Generator Vision and Architecture Specification

**Project:** Development and Evaluation of a Parametrizable Synthetic Data Generator for ML-Based Fraud Detection in ERP Systems  
**Process scope:** Procure-to-Pay in an educational SAP S/4HANA / Global Bike system  
**Interface scope:** SAP Fiori or browser-accessible SAP applications executed through Playwright tools  
**Document version:** 0.1  
**Status:** Working specification for implementation agents and thesis architecture planning

## 1. Purpose

This document specifies the intended architecture, execution model, responsibilities, interfaces, and implementation constraints for the synthetic ERP data generator. It is written as a stable target for implementation agents and as an architectural reference for the bachelor thesis.

The system is intended to generate labeled synthetic ERP process data by executing configured procure-to-pay process instances in a non-production SAP S/4HANA educational system. It creates business documents through the SAP user interface, records the execution, and later reconciles the resulting SAP data with the planned synthetic trace.

The central design decision is a strict separation between planning and execution:

- The **trace generator** plans what shall happen.
- The **generator** executes the planned trace without autonomous decisions.
- The **post-processor** reconciles planned and observed data and prepares the final synthetic dataset.

The generator is not an autonomous SAP agent. It is a deterministic interpreter of a structured execution schedule.

## 2. Architectural position

The system follows a **plan-execute-reconcile architecture**.

Planning happens before SAP execution. The trace generator consumes the compiled configuration, creates process instances, injects fraud scenarios, assigns actors, creates a dependency graph, and schedules executable steps. Execution happens through Playwright-based browser tools. Reconciliation happens after SAP execution, using the execution trace, execution logs, object registry, and exported SAP data sources.

The architecture deliberately avoids runtime LLM decisions in the generator. The LLM may be used only by the trace generator to transform descriptive realism criteria into structured parameters, to support scenario generation, and to refine scheduling realism. All LLM outputs must be converted into structured data and validated before execution.

## 3. Version-one scope

The first implementation version covers the standard procure-to-pay process with six core steps:

1. Create purchase requisition.
2. Create purchase order.
3. Release purchase order.
4. Post goods receipt.
5. Enter incoming invoice.
6. Post outgoing payment.

The first implementation version also supports fraud-related master-data manipulation required for **Vendor Flipflop**. Vendor Flipflop means that the bank account data of an existing Global Bike vendor is temporarily changed before payment and reverted after payment.

The first two fraud scenarios are:

- **Vendor Flipflop**, implemented as a controlled vendor bank-data manipulation around the payment step.
- **Larceny**, implemented as a controlled material or inventory misappropriation scenario. The exact SAP action sequence and Fiori tool for Larceny must still be fixed before implementation.

The version-one business simplification is **single-line purchasing**: each purchase requisition and purchase order contains one line item. The schemas must nevertheless model line items as arrays, so that multi-line documents can be added later without redesigning the trace format.

Parallel execution is in scope. The target is approximately 15 technical SAP users and approximately 15 concurrent browser actor_sessions. The exact concurrency limit remains configurable.

## 4. Explicit non-goals

The system is not intended for production SAP systems.

The generator must not use an LLM during SAP execution.

The generator must not decide which business step comes next.

The generator must not repair invalid traces by inventing missing parameters.

The generator must not use hidden marker fields, tax fields, reference texts, or artificial case tokens in SAP business documents solely for correlation.

The first version does not cover partial goods receipts, partial invoices, cancellations, reversals, returns, multi-line purchase orders, or autonomous exception handling.

The downstream fraud detection model is not the main contribution. It may be used only as an evaluation aid.

The post-processor is not expected to modify the SAP system. It transforms exported synthetic data.

## 5. Terminology

**Configuration system** means the Pkl-based source of configuration truth. It defines the configurable parameter space and compiles to YAML.

**Pkl** means the configuration-as-code language used for the source configuration. In this project, Pkl files are not consumed directly by the generator; they compile to YAML.

**Compiled YAML configuration** means the structured configuration artifact consumed by the trace generator.

**Virtual actor** means a synthetic employee identity used in the trace, execution log, actor assignment, labels, and final dataset.

**Technical SAP user** means a real SAP login account used by Playwright to execute Fiori interactions.

**Role constraint** means a software-enforced rule that restricts which virtual actor may execute which tool. Unless SAP authorizations are configured accordingly, role constraints are not SAP-enforced SoD.

**Tool** means a Python implementation that performs one atomic SAP interaction through Playwright, such as creating a purchase requisition.

**ToolSpec** means the structured metadata contract of a tool: input schema, output schema, preconditions, postconditions, generated objects, allowed roles, and idempotency policy.

**Tool catalogue** means the generated collection of ToolSpecs available to the trace generator and the configuration system.

**Trace generator** means the component that creates the planned process instances, dependency graph, scheduling information, labels, and post-processing instructions.

**Execution trace** means the complete planned trace artifact. It contains the business goal trace, dependency graph, executable schedule, labels, and post-processing metadata.

**Dependency graph** means a directed acyclic graph in which planned_steps are planned tool calls and dependencies are dependencies between tool calls.

**Planned Step** means one atomic planned tool call, for example `C042_A2_create_purchase_order`.

**Edge** means a dependency between two planned_steps, for example a purchase order planned step depending on the purchase requisition planned step.

**Scheduler** means the trace-generator subcomponent that converts the dependency graph into deterministic execution waves.

**Execution wave** means a deterministic batch of planned_steps that may be executed concurrently because their dependencies are satisfied and no actor/session constraints are violated.

**Generator** means the deterministic browser-execution component. It consumes the execution schedule and runs the tools.

**Runtime state store** means the generator-internal persistent store containing outputs produced during execution, such as generated purchase requisition and purchase order numbers.

**Object registry** means the persistent mapping between synthetic case IDs and real SAP object keys returned by tools.

**Execution log** means the event log written by the generator during execution.

**Post-processor** means the component that reconciles the execution trace, execution log, object registry, and exported SAP data sources to produce the final labeled dataset.

**Business date** means a date entered into, or stored in, a business document. Examples are document date, posting date, planned delivery date, baseline date, and due date.

**Target synthetic timestamp** means the intended timestamp in the final synthetic dataset. It may differ from the real wall-clock execution timestamp.

**Real execution timestamp** means the actual timestamp when Playwright executed a tool call.

**Case** means one planned process instance, for example one complete P2P execution for one material and one vendor.

**Scenario** means a normal or fraud-specific process template or graph transformation.

## 6. Monorepo components

The monorepo contains four primary components.

### 6.1 Configuration system

The configuration system defines the parameter space and compiles Pkl configuration into YAML.

It configures virtual actors, technical SAP users, role constraints, available tools, materials, vendors, plants, purchasing organizations, storage locations, value ranges, quantity ranges, calendars, working-hour models, fraud scenario proportions, and trace-generator settings.

### 6.2 Trace generator

The trace generator consumes the compiled YAML configuration and the tool catalogue. It creates the execution trace. It may call an LLM, but only during planning and only before the generator starts.

The trace generator owns:

- scenario selection;
- persona-to-parameter compilation;
- case generation;
- dependency graph construction;
- business-date planning;
- target synthetic timestamp generation;
- actor assignment;
- technical user/session planning;
- execution wave scheduling;
- label generation;
- deterministic trace validation.

### 6.3 Generator

The generator consumes the execution schedule and executes it. It opens browser actor_sessions, logs in with technical SAP users, invokes the specified tools, resolves dynamic placeholders, captures SAP object identifiers, updates the runtime state store, and writes execution logs.

The generator owns execution mechanics only. It does not own business planning.

### 6.4 Post-processor

The post-processor consumes the planned trace, execution log, object registry, and exported SAP data sources. It removes failed cases, shifts technical timestamps, replaces or adds actor identity fields, projects labels, and prepares the ML-facing synthetic dataset.

The first post-processing focus is business tables and change documents, especially CDHDR and CDPOS. Security audit logs and technical traces remain in scope but can be added after the first working version.

## 7. End-to-end data flow

1. Tool implementations expose ToolSpecs.
2. A discovery command creates or updates the tool catalogue.
3. The Pkl configuration references the tool catalogue and defines the generation scenario.
4. Pkl compiles into YAML.
5. The trace generator consumes YAML and the tool catalogue.
6. The trace generator creates a validated execution trace.
7. The scheduler inside the trace generator compiles the dependency graph into execution waves.
8. The generator consumes the execution waves.
9. The generator executes tools through Playwright and writes execution logs and object registry entries.
10. SAP creates business documents, change documents, logs, and traces.
11. SAP data is exported.
12. The post-processor reconciles exported SAP data with the execution trace and object registry.
13. The final output is a labeled synthetic ERP dataset.

## 8. Core invariants

The following rules are mandatory.

1. The generator must not call an LLM.
2. The generator must not choose, reorder, or invent business steps.
3. The trace generator must validate the dependency graph before scheduling.
4. The dependency graph must be acyclic.
5. No execution wave may contain two planned_steps where one depends on the other.
6. No virtual actor may execute two planned_steps at the same time.
7. No technical SAP user may be assigned to two concurrent browser actor_sessions unless this is explicitly supported and tested.
8. Every planned step must reference an existing tool in the tool catalogue.
9. Every tool input must be schema-validated before execution.
10. Every placeholder must resolve before the corresponding tool is started.
11. Every mutating tool that creates SAP objects must return structured generated SAP object keys.
12. Every generated SAP object key must be written to the runtime state store and object registry.
13. A missing required generated object key marks the planned step as failed.
14. A failed planned step marks the corresponding case as failed unless the tool explicitly declares the failure as non-mutating and recoverable.
15. Failed cases are excluded from the final ML-facing dataset by the post-processor.
16. Correlation must use the object registry, not only timestamps.
17. Artificial marker fields must not be inserted into SAP business documents for correlation.
18. Business dates and technical timestamps must be represented separately.
19. Final labels must be traceable to the scenario assignment in the execution trace.
20. All generated artifacts must include version and hash metadata.

## 9. Configuration design

### 9.1 What belongs in configuration

The configuration should define the intended data-generation experiment. It should not define browser mechanics.

Configurable elements include:

- number of cases;
- process types in scope;
- fraud share globally;
- fraud distribution per scenario;
- active fraud scenarios;
- material, vendor, plant, purchasing organization, storage location, and price/quantity constraints;
- virtual actors and role assignments;
- technical SAP user pool references;
- calendars, working hours, lunch breaks, time zones, and working-hour variation;
- human-behavior traits as natural language and, after compilation, numeric distributions;
- demand and shadow-inventory parameters;
- maximum concurrency;
- target generation time horizon;
- LLM model settings for the trace generator;
- scheduler parameters;
- post-processing configuration references.

### 9.2 What should stay hard-coded

The following should stay in code and not become normal experiment parameters:

| Area | Hard-coded or code-owned behavior |
|---|---|
| Browser mechanics | Fiori login, logout, launchpad reset, URL handling, browser context creation |
| Runtime protocol | How the generator invokes tools and captures tool results |
| Trace validation | Schema validation, placeholder validation, cycle checks |
| State handling | Runtime state store format and object registry update rules |
| Logging | Execution-log event schema |
| Failure control | Default failure classification and case exclusion policy |
| Tool implementation | Actual Playwright code for SAP UI interaction |
| Secrets | Passwords and tokens must be stored outside YAML/Pkl |
| Post-processing integrity | Referential-integrity checks and timestamp consistency checks |

### 9.3 Master-data validity matrix

The trace generator must not invent arbitrary SAP master-data combinations. It must sample from a configured master-data validity matrix.

Recommended structure:

```yaml
master_data:
  material_vendor_matrix:
    - material_id: "MA025"
      valid_vendors: ["V17121", "V17122"]
      valid_plants: ["MI00"]
      valid_purchasing_orgs: ["US00"]
      valid_storage_locations: ["0001", "0002"]
      quantity_range: {min: 1, max: 250}
      price_range: {min: 10.00, max: 120.00, currency: "USD"}
      delivery_lead_time_days: {min: 5, max: 21}
```

For version one, a simple shadow-inventory model is sufficient. It should track initial stock, demand rate, reorder threshold, and replenishment quantity range per material and plant. It should not attempt to fully simulate SAP inventory.

## 10. Technical user and actor model

The system should use multiple technical SAP users. This improves correlation in SAP logs because SAP fields such as user name can distinguish browser actor_sessions more reliably.

Recommended version-one rule:

- Configure approximately 15 technical SAP users.
- Map each active virtual actor to one technical SAP user where possible.
- If there are more virtual actors than technical users, reuse technical users only across non-overlapping execution windows.
- Do not allow two concurrent actor_sessions for the same technical SAP user unless this has been explicitly tested.
- Store credentials outside Pkl/YAML, for example as environment variables or secret references.

Even with multiple technical users, role constraints are still software-enforced unless the SAP accounts have corresponding SAP authorizations. Therefore, the thesis should distinguish between:

- **technical identity differentiation**, achieved by different SAP users;
- **SAP authorization-based SoD**, only present if SAP authorizations restrict users;
- **synthetic role separation**, implemented through trace and tool constraints.

The final ML-facing dataset may replace SAP technical users with virtual actors. The internal provenance layer should preserve both values.

Recommended user fields:

```yaml
identity_mapping:
  synthetic_actor_id: "procurement_01"
  synthetic_person_name: "Dieter Einkauf"
  role: "procurement"
  technical_sap_user: "GBGEN_P01"
  expose_in_final_dataset_as: "procurement_01"
```

## 11. Tool catalogue and tool interface

The tool catalogue is the stable interface between manually implemented SAP tools and the trace generator.

A tool implementation should expose a ToolSpec. The trace generator should not infer tool behavior from source code comments or natural language. A build-time command such as `discover-tools` should create the catalogue.

### 11.1 Required ToolSpec fields

```yaml
tool_name: "create_purchase_requisition"
tool_version: "1.0.0"
process_type: "procure_to_pay"
step_type: "create_purchase_requisition"
interface: "fiori_playwright"
input_schema: "schemas/CreatePurchaseRequisitionInput.json"
output_schema: "schemas/CreatePurchaseRequisitionOutput.json"
allowed_roles: ["procurement"]
required_sap_app: "TBD"
preconditions:
  - "material_vendor_combination_valid"
postconditions:
  - "purchase_requisition_created"
generated_objects:
  - object_type: "purchase_requisition"
    required_keys: ["pr_number"]
required_prior_outputs: []
idempotency_policy: "not_idempotent_after_save"
parallel_execution_safe: true
correlation_fields:
  - "pr_number"
```

### 11.2 Required tool result structure

Tools must return structured results. A human-readable SAP message can be included, but it must not be the only output. Tools should only return object keys that were observed from SAP or are deterministically guaranteed by the SAP response.

```yaml
tool_name: "create_purchase_requisition"
step_id: "C042_A1"
case_id: "C042"
started_at_real: "2026-04-30T09:01:22.341Z"
ended_at_real: "2026-04-30T09:02:10.907Z"
returned_objects:
  - object_type: "purchase_requisition"
    keys:
      pr_number: "0010051229"
sap_messages:
  - severity: "success"
    text: "Purchase requisition 0010051229 created"
raw_observations:
  status_bar_text: "Purchase requisition 0010051229 created"
```

If a required object key cannot be extracted from SAP, the tool must fail or the generator must classify the planned step as failed. The tool must not invent item keys such as `00010` unless it actually extracted or otherwise proved that value.

## 12. Execution trace structure

The execution trace should contain both the planning graph and the executable schedule.

Top-level structure:

```yaml
trace_version: "0.2"
run_id: "RUN_2026_04_30_001"
config_hash: "..."
tool_catalog_hash: "..."
trace_generator_version: "..."
llm_metadata:
  used: true
  model: "TBD"
  prompt_set_version: "..."
  seed: null
cases: []
dependency_graph:
  planned_steps: []
  dependencies: []
execution_schedule:
  mode: "waves"
  max_parallel_actor_sessions: 15
  waves: []
post_processing_plan: {}
```

### 12.1 Case structure

```yaml
case_id: "C042"
process_type: "procure_to_pay"
case_scenario_type: "NORMAL"
line_items:
  - line_id: "C042_L1"
    material_id: "MA025"
    vendor_id: "V17121"
    plant: "MI00"
    purchasing_org: "US00"
    storage_location: "0001"
    quantity: 25
    target_price: 42.50
```

### 12.2 Planned Step structure

```yaml
planned_step_id: "C042_A2"
case_id: "C042"
step_type: "create_purchase_order"
tool_name: "create_purchase_order"
synthetic_actor_id: "procurement_01"
technical_sap_user_id: "GBGEN_P01"
inputs:
  purchase_requisition_number: "$purchase_requisition.pr_number"
  purchasing_org: "US00"
  purchasing_group: "001"
  vendor_id: "V17121"
required_sap_object_keys:
  - "purchase_order.po_number"
planned_date_inputs:
  document_date: "2026-02-03"
  planned_delivery_date: "2026-02-17"
planned_synthetic_time:
  start: "2026-02-03T10:14:00-05:00"
  end: "2026-02-03T10:19:00-05:00"
labels:
  step_label: "normal"
```

### 12.3 Dependency structure

```yaml
from_planned_step_id: "C042_A1"
to_planned_step_id: "C042_A2"
type: "data_dependency"
reason: "purchase order requires purchase requisition number"
```

Recommended edge types:

- `data_dependency`
- `business_dependency`
- `authorization_or_role_dependency`
- `temporal_dependency`
- `accounting_dependency`
- `fraud_dependency`
- `fraud_cleanup_dependency`

## 13. Runtime state store and dynamic variables

Some SAP object identifiers are not known when the trace is generated. Examples are purchase requisition number, purchase order number, material document number, invoice document number, accounting document number, and vendor-change document number.

The generator must maintain a runtime state store. This store is both internal execution memory and the basis for the object registry.

Required behavior:

1. Before starting a planned step, the generator resolves all placeholders in the planned step input.
2. If any placeholder is unresolved, the planned step fails before SAP interaction.
3. After successful tool execution, the generator writes all returned object keys to the state store.
4. The state update is persisted before any dependent planned step is started.
5. The execution log records the state update.

Recommended structure:

```yaml
runtime_state:
  run_id: "RUN_2026_04_30_001"
  cases:
    C042:
      status: "running"
      outputs:
        C042_A1:
          purchase_requisition:
            pr_number: "0010051229"
        C042_A2:
          purchase_order:
            po_number: "4500008732"
```

The runtime state store may be implemented as SQLite, JSONL plus index file, or another persistent structure. SQLite is recommended if resume, querying, and safe concurrent updates are required.

## 14. Dependency graph model

The dependency graph is the planning representation. It expresses what must happen before what, and why.

### 14.1 Normal P2P graph

```text
A1 create_purchase_requisition
  -> A2 create_purchase_order
  -> A3 release_purchase_order
  -> A4 post_goods_receipt
  -> A5 enter_incoming_invoice
  -> A6 post_outgoing_payment
```

### 14.2 Vendor Flipflop graph

```text
A1 create_purchase_requisition
  -> A2 create_purchase_order
  -> A3 release_purchase_order
  -> A4 post_goods_receipt
  -> A5 enter_incoming_invoice
  -> F1 change_vendor_bank_data
  -> A6 post_outgoing_payment
  -> F2 revert_vendor_bank_data
```

The critical fraud dependency is that payment must occur while the manipulated bank data is active.

### 14.3 Larceny graph

The exact Larceny implementation must still be fixed. The graph should be modeled as a graph extension after goods receipt or inventory availability.

Example pattern:

```text
A1 create_purchase_requisition
  -> A2 create_purchase_order
  -> A3 release_purchase_order
  -> A4 post_goods_receipt
  -> F1 post_inventory_reduction_or_scrap
  -> A5 enter_incoming_invoice
  -> A6 post_outgoing_payment
```

The concrete SAP tool for `post_inventory_reduction_or_scrap` is TBD.

## 15. Scheduler and execution waves

The scheduler belongs to the trace generator, not to the generator.

The scheduler converts the dependency graph into deterministic execution waves. A wave is not a conversational round and it is not necessarily one task per actor. A wave is a bounded set of planned_steps that may run concurrently because all dependencies are already satisfied and no configured capacity or actor constraint is violated.

A virtual actor can be idle in a wave. A wave does not require every actor to receive a task.

Within a wave, planned_steps must not have business dependencies on one another. The generator may start them concurrently with a fixed worker limit. For reproducibility, the wave may still define a stable startup order.

### 15.1 Scheduling constraints

The scheduler must consider:

- dependency graph dependencies;
- virtual actor availability;
- working hours;
- lunch breaks;
- time zones;
- role constraints;
- technical SAP user assignment;
- maximum browser actor_sessions;
- planned delivery lead time;
- business-date constraints;
- target synthetic timestamps;
- fraud-specific ordering constraints.

### 15.2 Execution wave structure

```yaml
execution_schedule:
  mode: "waves"
  max_parallel_sessions: 15
  waves:
    - wave_id: "W001"
      sequence_no: 1
      planned_steps:
        - planned_step_id: "C001_A1"
          startup_order: 1
        - planned_step_id: "C002_A1"
          startup_order: 2
        - planned_step_id: "C003_A1"
          startup_order: 3
    - wave_id: "W002"
      sequence_no: 2
      planned_steps:
        - planned_step_id: "C001_A2"
          startup_order: 1
        - planned_step_id: "C004_A1"
          startup_order: 2
```

### 15.3 Scheduling algorithm

Recommended algorithm:

1. Build case graphs from process templates and fraud scenario transformations.
2. Merge case graphs into one run-level dependency graph.
3. Validate the graph is acyclic.
4. Assign actors according to role constraints and calendars.
5. Assign planned date inputs and target synthetic timestamps.
6. Maintain a ready set of planned_steps whose dependencies are satisfied.
7. Sort ready planned_steps by target synthetic start time and priority.
8. Pack ready planned_steps into the next wave subject to concurrency, actor, technical user, and session constraints.
9. Verify that the wave contains no intra-wave dependency edge.
10. Continue until all planned_steps are scheduled.
11. Emit target and actual fraud counts, scheduling statistics, and validation report.

## 16. Business dates, target timestamps, and process realism

The system must distinguish planned date inputs from technical timestamps.

Business dates should be entered into SAP during execution whenever possible. Examples include document date, posting date, planned delivery date, baseline date, and due date.

Target synthetic timestamps are planned by the trace generator and applied to exported technical timestamps by the post-processor. Examples include creation time, change time, log time, trace time, and event time.

The generator should not wait weeks for a delivery. It executes in real wall-clock time. The trace generator plans the synthetic process timeline, and the post-processor shifts technical timestamps accordingly.

The trace generator must model realistic process timing, including:

- actor working hours;
- lunch breaks;
- time zones;
- department queues;
- inter-step delays;
- delivery lead times;
- invoice and payment timing;
- fraud-specific timing windows;
- persona-dependent work speed.

Example: if the planned delivery date is two weeks after the purchase order, the goods receipt planned step must receive a business posting date near that delivery date and a target synthetic timestamp near that delivery date. The generator may execute it immediately in real time, but the final dataset should show the planned synthetic time.

## 17. Generator execution contract

For each planned step, the generator must perform the following deterministic procedure:

1. Read the planned step from the current execution wave.
2. Check that the case is not failed.
3. Resolve placeholders from the runtime state store.
4. Validate final tool inputs against the tool input schema.
5. Select the configured technical SAP user/session.
6. Reset browser state to the configured stable start state.
7. Invoke the referenced tool.
8. Capture structured tool result.
9. Validate required returned object keys.
10. Persist returned SAP object keys in the runtime state store.
11. Write execution log events.
12. Mark planned step successful or failed.
13. If failed, mark the case failed and skip dependent planned_steps of that case.

The generator may manage concurrency and browser resources. It must not change business intent.

## 18. Execution log and object registry

The execution log should be append-only. JSONL is recommended for implementation simplicity.

Minimum event types:

- `run_started`
- `wave_started`
- `node_started`
- `tool_invoked`
- `tool_result_received`
- `state_updated`
- `planned_step_succeeded`
- `planned_step_failed`
- `case_failed`
- `wave_finished`
- `run_finished`

Required planned step log fields:

```yaml
run_id: "RUN_2026_04_30_001"
case_id: "C042"
planned_step_id: "C042_A2"
wave_id: "W007"
synthetic_actor_id: "procurement_01"
technical_sap_user: "GBGEN_P01"
actor_session_id: "session_03"
tool_name: "create_purchase_order"
real_start_time: "2026-04-30T09:05:11.122Z"
real_end_time: "2026-04-30T09:07:49.883Z"
planned_synthetic_start_time: "2026-02-03T10:14:00-05:00"
planned_synthetic_end_time: "2026-02-03T10:19:00-05:00"
status: "success"
input_parameters: {}
resolved_input_parameters: {}
returned_objects: []
sap_messages: []
```

Object registry fields:

```yaml
run_id: "RUN_2026_04_30_001"
case_id: "C042"
planned_step_id: "C042_A2"
case_scenario_type: "NORMAL"
synthetic_actor_id: "procurement_01"
technical_sap_user: "GBGEN_P01"
sap_object_type: "purchase_order"
sap_object_id: "4500008732"
sap_object_item: "00010"
sap_object_year: null
company_code: "US00"
parent_object_type: "purchase_requisition"
parent_object_id: "0010051229"
status: "created"
```

The object registry is the primary correlation source for post-processing. Timestamps are secondary evidence.

## 19. Failure handling

Version one should use conservative case-level failure handling.

If a mutating tool fails, the corresponding case is marked failed. A mutating tool is any tool that creates, changes, releases, posts, deletes, blocks, or otherwise persists SAP data.

Blind retries after a save/post action are not allowed. They can create duplicate business objects.

A technical retry may be allowed only before persistence, for example when navigation fails before pressing Save/Post. If the failure happens after a persistence action, the tool must first determine whether an SAP object was created.

Failure log fields:

```yaml
status: "failed"
failure_type: "navigation_error | sap_validation_error | post_save_unknown_state | required_output_missing"
failed_planned_step_id: "C042_A4"
case_exclusion_required: true
created_objects_before_failure: []
```

The post-processor removes failed cases from the final ML-facing dataset using the object registry.

## 20. Fraud scenario model

Fraud scenarios should be implemented as graph transformations over a legitimate base process. This keeps the normal P2P process stable and makes fraud injection explicit.

### 20.1 Vendor Flipflop

Vendor Flipflop adds two master-data manipulation planned_steps:

- `change_vendor_bank_data` before payment;
- `revert_vendor_bank_data` after payment.

Required graph condition:

```text
change_vendor_bank_data -> post_outgoing_payment -> revert_vendor_bank_data
```

Required evidence to log:

- vendor ID;
- original bank data;
- manipulated bank data;
- technical SAP user;
- virtual actor;
- change-document object key if available;
- payment document key;
- revert success.

This scenario temporarily changes existing Global Bike vendor bank data. Because this modifies existing master data, the revert step is mandatory. A failed revert must be logged as a critical cleanup failure.

### 20.2 Larceny

Larceny is in scope, but the exact SAP implementation must be fixed before coding. The scenario should represent misappropriation of company assets or inventory. A plausible version-one pattern is receiving goods and then posting an inventory reduction, scrap movement, or similar event that masks asset removal.

Required decision before implementation:

- Which Fiori app/tool performs the inventory reduction or scrap posting?
- Which SAP tables and logs should contain evidence?
- Does the scenario occur before or after invoice/payment?
- Is the fraudulent actor warehouse, procurement, or another role?

## 21. Labeling model

The final dataset should support multi-level labels.

Recommended scenario and label fields:

```yaml
case_scenario_type: "NORMAL | VENDOR_FLIPFLOP | LARCENY"
scenario_family: "none | vendor_master_manipulation | inventory_misappropriation"
step_label: "normal | fraud_step | fraud_supporting_step | cleanup_step"
object_label: "unaffected | affected_by_fraud"
visibility_sources:
  - "business_table"
  - "change_document"
  - "execution_log"
  - "security_audit_log"
```

Use the term **synthetically injected fraud scenario** in the thesis where appropriate. The label does not prove real-world fraud intent. It identifies an intentionally generated fraud-like scenario in the synthetic experiment.

## 22. Post-processing concept

The post-processor should operate on exported data, not on the live SAP system.

Responsibilities:

- remove failed cases;
- shift technical timestamps to target synthetic timestamps;
- preserve event ordering;
- map technical SAP users to virtual actors in the final dataset;
- preserve original SAP users in a provenance layer;
- attach labels;
- reconcile exported business tables with the object registry;
- validate referential integrity.

The first minimum post-processing target is:

- business tables required for P2P;
- CDHDR;
- CDPOS.

Security audit logs and traces remain in scope but may follow after the first working business-table and change-document pipeline.

Post-processing mapping should be table-specific:

```yaml
table: "CDHDR"
key_fields: ["OBJECTCLAS", "OBJECTID", "CHANGENR"]
case_id_resolution: "object_registry"
timestamp_fields: ["UDATE", "UTIME"]
user_fields: ["USERNAME"]
actor_mapping: "technical_user_to_virtual_actor"
label_mapping: "case_and_step_labels"
```

## 23. Acceptance criteria

### 23.1 Configuration system

- Pkl compiles to YAML without manual edits.
- YAML contains no plaintext credentials.
- Master-data combinations validate before trace generation.
- Fraud proportions are recorded as target and actual counts.

### 23.2 Trace generator

- Dependency graph is acyclic.
- All planned_steps reference valid tools.
- All role assignments are valid.
- All business-date constraints are valid.
- All waves contain only dependency-independent planned_steps.
- No actor or technical user is double-booked within a wave.
- Trace contains target and actual fraud counts.
- Trace contains a validation report.

### 23.3 Generator

- Generator never calls an LLM.
- Generator resolves placeholders only from runtime state.
- Generator fails the planned step if required placeholders are missing.
- Generator writes structured execution logs.
- Generator writes object registry entries for every generated SAP object.
- Generator marks failed cases explicitly.
- Generator does not insert artificial correlation markers into SAP business documents.

### 23.4 Post-processor

- Failed cases are excluded by object registry, not by timestamp window alone.
- Technical timestamps are shifted without violating process order.
- Business dates and technical timestamps remain distinguishable.
- Labels are traceable to the execution trace.
- Original technical SAP users are preserved in provenance data.

## 24. Evaluation hooks for the thesis

The generator should be evaluated as an architecture and implementation artifact, not primarily as a fraud-detection model.

Recommended evaluation dimensions:

- execution success rate;
- case failure rate;
- object registry completeness;
- planned-versus-executed step match;
- dependency-graph validity;
- scheduling validity;
- tool schema coverage;
- trace reproducibility at artifact level;
- post-processing consistency;
- label projection correctness;
- generated fraud distribution versus target distribution;
- runtime and scalability under configured concurrency;
- qualitative realism of schedules and actor behavior.

## 25. Implementation roadmap

### Phase 0: Stable schemas

Define ToolSpec, execution trace schema, planned step schema, edge schema, execution wave schema, runtime state schema, and object registry schema.

### Phase 1: Tool catalogue

Implement tool discovery and catalogue export. Ensure every tool returns structured outputs.

### Phase 2: Configuration MVP

Implement Pkl configuration for actors, technical users, master data, fraud proportions, and available scenarios. Compile to YAML.

### Phase 3: Trace generator MVP

Generate normal P2P cases, build the dependency graph, validate the graph, schedule execution waves, and emit trace artifacts.

### Phase 4: Generator runtime MVP

Execute normal P2P waves with multiple technical SAP users, runtime state store, placeholder resolution, execution logging, and object registry.

### Phase 5: Fraud scenarios

Add Vendor Flipflop first. Add Larceny after the exact SAP tool sequence is fixed.

### Phase 6: Post-processing MVP

Export and reconcile business tables plus CDHDR/CDPOS. Remove failed cases and project labels.

### Phase 7: Evaluation

Run controlled experiments and report success rate, object registry completeness, label correctness, and realism assessment.

## 26. Open issues

The following decisions remain open and must be resolved before implementation or thesis finalization:

1. Exact Fiori app or URL for vendor bank-data modification.
2. Exact implementation variant for Larceny.
3. Exact SAP table and field mapping for all P2P artifacts in the Global Bike S/4HANA system.
4. Whether technical SAP users receive real SAP authorization restrictions or merely distinct usernames.
5. Security Audit Log and trace fields available for correlation under multiple browser actor_sessions.
6. Exact treatment of payment terms, discounts, baseline dates, and due dates in version one.
7. Whether final user fields are replaced by virtual actors or virtual actors are added as additional columns.
8. Exact original source citation for the fraud scenario catalogue based on Tritscher et al.

## Appendix A: Minimal normal P2P trace excerpt

```yaml
case_id: "C001"
process_type: "procure_to_pay"
case_scenario_type: "NORMAL"

planned_steps:
  - planned_step_id: "C001_A1"
    step_type: "create_purchase_requisition"
    tool_name: "create_purchase_requisition"
    synthetic_actor_id: "procurement_01"
    technical_sap_user_id: "GBGEN_P01"
    inputs:
      material_id: "MA025"
      quantity: 25
      plant: "MI00"
      purchasing_group: "001"
      vendor_id: "V17121"
    required_sap_object_keys: ["purchase_requisition.pr_number"]

  - planned_step_id: "C001_A2"
    step_type: "create_purchase_order"
    tool_name: "create_purchase_order"
    synthetic_actor_id: "procurement_01"
    technical_sap_user_id: "GBGEN_P01"
    inputs:
      purchase_requisition_number: "$purchase_requisition.pr_number"
      vendor_id: "V17121"
    required_sap_object_keys: ["purchase_order.po_number"]

dependencies:
  - from_planned_step_id: "C001_A1"
    to_planned_step_id: "C001_A2"
    type: "data_dependency"
    reason: "PO requires PR number"
```

## Appendix B: Minimal Vendor Flipflop trace excerpt

```yaml
case_id: "C077"
process_type: "procure_to_pay"
case_scenario_type: "VENDOR_FLIPFLOP"

dependencies:
  - from_planned_step_id: "C077_A5"
    to_planned_step_id: "C077_F1"
    type: "business_dependency"
    reason: "invoice exists before bank-data manipulation"
  - from_planned_step_id: "C077_F1"
    to_planned_step_id: "C077_A6"
    type: "fraud_dependency"
    reason: "payment must occur while manipulated bank data is active"
  - from_planned_step_id: "C077_A6"
    to_planned_step_id: "C077_F2"
    type: "fraud_cleanup_dependency"
    reason: "vendor bank data must be reverted after payment"
```

## Appendix C: Implementation directives for agents

Implementation agents should follow these directives:

1. Build schemas before building logic.
2. Do not implement runtime LLM use in the generator.
3. Do not add SAP marker fields for correlation.
4. Use the object registry as the primary correlation mechanism.
5. Use placeholders for unknown SAP identifiers.
6. Persist runtime state before executing dependent planned_steps.
7. Keep the scheduler inside the trace generator.
8. Keep browser mechanics inside the generator.
9. Keep business scenario logic out of the generator.
10. Fail conservatively when SAP persistence state is unclear.
11. Add new processes by adding process templates, tool specs, and scenario transformations, not by rewriting the generator.

## References to project context

- Schnepf, Engin, Anderer, and Scheuermann: Studies on the Use of Large Language Models for the Automation of Business Processes in Enterprise Resource Planning Systems.
- Schnepf, Schwarz, Scheuermann, and Anderer: A Study on Multi-Agent Collaboration for Business Process Automation in Enterprise Resource Planning Systems.
- Engin: Prozessautomatisierung von SAP ERP durch die Nutzung von Large Language Modellen.
- Schwarz: Automatisierung von SAP ERP durch den Einsatz eines LLM-basierten Multi-Agenten-Systems.
- Vetter: Datenmanagement und Datenanalyse für die Erkennung von Betrugsfällen in SAP-ERP-Systemen.
- Tritscher et al.: Original source for the fraud scenario catalogue must be cited directly in the thesis.
