# Synthetic ERP Data Generator

This context describes the domain language for planning, executing, and reconciling synthetic ERP process traces. **Synthetic ERP Data Generator** is the canonical umbrella term for the whole system.

## Language

**Trace Generator**:
The planning component that creates process cases, actor assignments, planned date inputs, dependencies, and execution waves.
_Avoid_: Planner, scheduler, generator core

**Trace Executor**:
The execution component that runs an execution trace against SAP Fiori through deterministic browser tools.
_Avoid_: Browser agent, SAP agent, runner

**Execution Trace**:
A planned process artifact that lists actor_sessions, process cases, planned steps, dependencies, execution waves, inputs, and required SAP object keys.
_Avoid_: Script, workflow file, task list

**Execution Run**:
One invocation of the trace executor against one execution trace.
_Avoid_: Run, execution, session, trace run

**Execution Wave**:
A group of planned steps the trace executor may start in the same scheduling round.
_Avoid_: Batch, parallel group, stage

**Planned Synthetic Time**:
The planned synthetic timestamp interval assigned to a planned step for final dataset chronology.
_Avoid_: Synthetic time, target time, runtime time

**Planned Date Input**:
A planned date value supplied to a browser tool input or preserved for post-processing when SAP cannot accept it at runtime.
_Avoid_: Business date, execution date, date override, runtime date

**Actor Session**:
One executor session for a synthetic actor during an execution run, authenticated through a technical SAP user.
_Avoid_: SAP session, browser session, login session

**Post-Processing Manifest**:
A reconciliation artifact that describes how planned trace data should be joined with execution evidence and SAP exports.
_Avoid_: Export config, reconciliation config

**Post-Processor**:
The component that uses planned artifacts, execution evidence, and SAP exports to produce the final synthetic dataset.
_Avoid_: Reconciler, export processor, dataset builder

**SAP Export**:
Raw or extracted SAP data used by the post-processor as observed system data.
_Avoid_: Report, table dump, export group

**Synthetic Dataset**:
The final dataset produced by the post-processor after applying planned timing, actor projection, labels, and failure policy.
_Avoid_: Final output, generated data, reconciled export

**Execution Evidence**:
The logical set of runtime artifacts produced by an execution run.
_Avoid_: Canonical evidence file, combined evidence file, output bundle

**Process Case**:
One planned business-process instance in an execution trace.
_Avoid_: Case, scenario, process instance, trace case

**Failed Process Case**:
A process case marked failed because at least one planned step in that case failed or was interrupted during an execution run.
_Avoid_: Failed case, failed node, failed execution

**Case Scenario Type**:
A configured category that describes whether and how a process case is normal or intentionally anomalous.
_Avoid_: Scenario, fraud scenario, process scenario

**Process Definition**:
A configured template for a business process such as procure-to-pay, made of process steps and dependencies.
_Avoid_: Process template, workflow, scenario

**Process Step**:
A reusable business step in a configured process template.
_Avoid_: Task type, node type, activity

**Process Dependency**:
A rule that one process step must happen after another within a process definition.
_Avoid_: Edge, graph dependency, ordering rule

**Planned Step**:
One concrete scheduled occurrence of a process step inside a process case.
_Avoid_: Trace node, task, work item, execution step

**Browser Tool**:
A deterministic browser operation assigned to a process step.
_Avoid_: SAP tool, action, browser script, agent tool

**Input Binding**:
A declarative rule that fills a planned step input from configuration, process-case data, planned dates, or prior returned object keys.
_Avoid_: Input map, parameter mapping, prompt variable

**Configured Master Data**:
Repository configuration that constrains which SAP materials, vendors, plants, purchasing organizations, storage locations, quantities, prices, and dates the trace generator may sample.
_Avoid_: Master data, SAP master data, fixture data

**SAP Business Object**:
A business record in SAP that can be created, observed, and referenced by keys.
_Avoid_: Object, entity, record

**SAP Object Key**:
A stable identifier field for a SAP business object.
_Avoid_: Output, ID, key

**Runtime State**:
The process-scoped store of SAP object keys observed while executing a trace.
_Avoid_: Global state, cache, memory

**Returned Object**:
A structured tool-result entry that identifies a SAP business object created or observed by a tool and the keys that can be reused later.
_Avoid_: Output blob, flat result

**Execution Log**:
A JSONL evidence artifact containing lifecycle and planned-step events for one executor run.
_Avoid_: Terminal log, trace log

**Object Registry**:
A JSONL evidence artifact containing SAP object keys captured during execution.
_Avoid_: Object cache, state dump

**Object Lineage**:
The ordered chain of SAP business objects expected for a process case.
_Avoid_: Object chain, lineage chain, relationship chain

**Synthetic Actor**:
A planned business persona that owns synthetic work timing and final dataset identity.
_Avoid_: User, account, technical user

**Realism Guardrails**:
Configured hard bounds that constrain LLM-generated timing and behavior values before the **Trace Generator** schedules planned work.
_Avoid_: Prompt hints, soft preferences, unvalidated realism

**Compiled Realism Criteria**:
Validated structured realism data produced from natural-language descriptions and **Realism Guardrails** before scheduling.
_Avoid_: Raw LLM answer, prompt output, schedule

**Demand Pattern**:
A compact daily demand shape that the **Trace Generator** expands into exact **Demand Releases**.
_Avoid_: Demand batch, demand prompt, generated cases

**Demand Release**:
The planned moment when a **Process Case** becomes available for its first **Planned Step**.
_Avoid_: Case start, order date, task start

**Requested Delivery Date**:
The requested warehouse delivery date used as the process case's planned delivery input and earliest goods-receipt gate.
_Avoid_: Actual arrival date, runtime receipt date, warehouse arrival

**Price Anchor**:
A per-material price center used to sample realistic target prices inside configured price guardrails.
_Avoid_: Fixed price, raw price range, case price

**Material Demand Profile**:
A per-material horizon profile used by the **Trace Generator** to allocate configured materials to exact **Process Cases**.
_Avoid_: Material mix, material probability, material prompt output

**Relative Demand Weight**:
A positive integer emitted for a configured material that the **Trace Generator** normalizes into exact material counts.
_Avoid_: Probability, percentage, share

**Quantity Profile**:
A per-material order-quantity model containing typical quantity, variation, bulk share, and order multiple inside configured hard quantity guardrails.
_Avoid_: Quantity range, random amount, SAP quantity

**Actor Day Profile**:
One date-specific timing profile for a **Synthetic Actor** derived from its baseline realism model and daily workload.
_Avoid_: Daily actor criteria, day mood, runtime delay profile

**Workload Intensity**:
A daily demand pressure category that can softly influence **Actor Day Profiles**.
_Avoid_: Capacity, backlog, utilization

**Human Delay Profile**:
Runtime-safe actor behavior metadata that lets the **Trace Executor** apply bounded human-like delays without reading planning cache artifacts.
_Avoid_: Speed factor, runtime prompt, tool input

**Runtime Delay Marker**:
A named point inside a **Browser Tool** where the **Trace Executor** may pause using the active actor's **Human Delay Profile**.
_Avoid_: Sleep, artificial wait, SAP wait

**Actor Capability**:
The set of process steps a synthetic actor is allowed to perform.
_Avoid_: Role, permission, assignment

**Actor Projection**:
A post-processing mapping that exposes synthetic actors in the synthetic dataset while hiding technical SAP user identity.
_Avoid_: User projection, identity mapping, actor mapping

**Technical SAP User**:
A real SAP login account referenced by environment variables and mapped to one or more synthetic actors.
_Avoid_: Actor, business user, persona

## Relationships

- A **Trace Generator** produces an **Execution Trace** and a **Post-Processing Manifest**.
- A **Trace Executor** consumes exactly one **Execution Trace** for an **Execution Run**.
- A **Post-Processor** consumes an **Execution Trace**, a **Post-Processing Manifest**, **Execution Evidence**, and **SAP Exports**.
- A **Post-Processor** produces a **Synthetic Dataset**.
- A **Process Definition** produces one or more **Process Cases** in an **Execution Trace**.
- A **Process Definition** contains one or more **Process Dependencies**.
- **Configured Master Data** constrains the process cases generated from a process definition.
- An **Execution Trace** contains one or more **Process Cases**.
- A **Process Case** has exactly one **Case Scenario Type**.
- A **Process Case** contains one or more **Planned Steps**.
- A failed **Planned Step** can cause its **Process Case** to become a **Failed Process Case**.
- A **Planned Step** is created from exactly one **Process Step**.
- A **Process Step** is assigned to exactly one **Browser Tool**.
- A **Process Step** has one or more **Input Bindings**.
- An **Execution Wave** contains one or more **Planned Steps**.
- An **Execution Wave** permits concurrency, but actual parallelism depends on actor and technical-user constraints.
- A **Planned Step** has exactly one **Planned Synthetic Time** interval.
- A **Planned Step** may have one or more **Planned Date Inputs**.
- An **Execution Trace** contains one or more **Actor Sessions**.
- A **Synthetic Actor** maps to exactly one **Technical SAP User** during execution.
- A **Synthetic Actor** may have **Realism Guardrails** that limit its **Compiled Realism Criteria**.
- **Compiled Realism Criteria** can contain **Demand Patterns**, **Price Anchors**, **Material Demand Profiles**, and **Actor Day Profiles**.
- A **Demand Pattern** produces one or more **Demand Releases**.
- A **Process Case** has exactly one **Demand Release**.
- A **Process Case** has exactly one **Requested Delivery Date**.
- A **Price Anchor** belongs to exactly one configured material.
- A **Material Demand Profile** belongs to exactly one configured material.
- A **Material Demand Profile** has exactly one **Relative Demand Weight**.
- A **Material Demand Profile** has exactly one **Quantity Profile**.
- The **Trace Generator** normalizes **Relative Demand Weights** into exact material assignments before writing the **Execution Trace**.
- The **Trace Generator** samples process-case quantities from **Quantity Profiles** before writing the **Execution Trace**.
- The **Trace Executor** does not normalize material demand, sample quantities, or perform scheduling math.
- A **Workload Intensity** can influence one or more **Actor Day Profiles**.
- A **Technical SAP User** may back multiple **Synthetic Actors**.
- Scheduling prevents one **Technical SAP User** from running multiple **Planned Steps** at the same time.
- An **Actor Session** belongs to exactly one **Synthetic Actor** and is authenticated through exactly one **Technical SAP User**.
- A **Synthetic Actor** has one or more **Actor Capabilities**.
- An **Actor Projection** controls how a **Synthetic Actor** appears in the **Synthetic Dataset**.
- A **SAP Business Object** has one or more **SAP Object Keys**.
- A **Returned Object** identifies one **SAP Business Object** and its reusable **SAP Object Keys**.
- A **Returned Object** updates **Runtime State** and may be written to the **Object Registry**.
- **Object Lineage** describes the expected SAP business object chain for one **Process Case**.
- **Execution Evidence** includes the **Execution Log** and the **Object Registry**.
- **Execution Evidence** provides runtime input for the **Post-Processing Manifest**.
- A **Runtime Delay Marker** uses a **Human Delay Profile** from the **Execution Trace**, not the **Compiled Realism Criteria** cache.

## Example dialogue

> **Dev:** "When the **Trace Executor** creates a purchase requisition in a **Process Case**, should the next **Planned Step** read the number from the **Execution Trace**?"
> **Domain expert:** "No. The **Execution Trace** plans the placeholder, and the **Runtime State** resolves it from a **Returned Object** captured during execution."

## Flagged ambiguities

- "generator" can mean the whole repository, the **Trace Generator**, or the **Trace Executor**. Use the specific term when discussing component responsibility.
- "Advanced Synthetic ERP Data Generator" names the repository, but **Synthetic ERP Data Generator** is the canonical product/context term.
- "case" should mean **Process Case** when referring to planned business-process instances.
- "scenario" should mean **Case Scenario Type** when referring to normal or intentionally anomalous process cases.
- **Case Scenario Type** is the source of truth for process-case classification.
- "node" and "task" should mean **Planned Step** when referring to a concrete scheduled occurrence.
- **Execution Trace** is planned executor input, not runtime evidence, despite the word "execution."
- "run" should mean **Execution Run** when referring to one executor invocation.
- "session" should mean **Actor Session** when discussing logical execution; "browser session" is an implementation detail.
- **Execution Evidence** is a logical set of artifacts, not one canonical combined file.
- "user" can mean **Synthetic Actor** or **Technical SAP User**. Use **Synthetic Actor** for planned business identity and **Technical SAP User** for SAP credentials.
