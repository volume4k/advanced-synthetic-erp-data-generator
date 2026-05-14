# Synthetic ERP Data Generator

This context describes the domain language for planning, executing, and reconciling synthetic ERP process traces.

## Language

**Trace Generator**:
The planning component that creates process cases, actor assignments, business dates, dependencies, and execution waves.
_Avoid_: Planner, scheduler, generator core

**Trace Executor**:
The execution component that runs an execution trace against SAP Fiori through deterministic browser tools.
_Avoid_: Browser agent, SAP agent, runner

**Execution Trace**:
A planned process artifact that lists sessions, cases, nodes, dependencies, execution waves, inputs, and expected outputs.
_Avoid_: Script, workflow file, task list

**Post-Processing Manifest**:
A reconciliation artifact that describes how planned trace data should be joined with execution evidence and SAP exports.
_Avoid_: Export config, reconciliation config

**Runtime State**:
The process-scoped store of SAP object keys observed while executing a trace.
_Avoid_: Global state, cache, memory

**Returned Object**:
A structured tool-result entry that identifies a SAP object created or observed by a tool and the keys that can be reused later.
_Avoid_: Output blob, flat result

**Execution Log**:
A JSONL evidence artifact containing lifecycle and node events for one executor run.
_Avoid_: Terminal log, trace log

**Object Registry**:
A JSONL evidence artifact containing SAP object keys captured during execution.
_Avoid_: Object cache, state dump

**Synthetic Actor**:
A planned business persona that owns synthetic work timing and final dataset identity.
_Avoid_: User, account, technical user

**Technical SAP User**:
A real SAP login account referenced by environment variables and mapped to one or more synthetic actors.
_Avoid_: Actor, business user, persona

## Relationships

- A **Trace Generator** produces an **Execution Trace** and a **Post-Processing Manifest**.
- A **Trace Executor** consumes exactly one **Execution Trace** for a run.
- An **Execution Trace** contains one or more **Synthetic Actors** and references **Technical SAP Users** through session blocks.
- A **Synthetic Actor** maps to exactly one **Technical SAP User** during execution.
- A **Returned Object** updates **Runtime State** and may be written to the **Object Registry**.
- An **Execution Log** and an **Object Registry** provide runtime evidence for the **Post-Processing Manifest**.

## Example dialogue

> **Dev:** "When the **Trace Executor** creates a purchase requisition, should the next node read the number from the **Execution Trace**?"
> **Domain expert:** "No. The **Execution Trace** plans the placeholder, and the **Runtime State** resolves it from a **Returned Object** captured during execution."

## Flagged ambiguities

- "generator" can mean the whole repository, the **Trace Generator**, or the **Trace Executor**. Use the specific term when discussing component responsibility.
- "user" can mean **Synthetic Actor** or **Technical SAP User**. Use **Synthetic Actor** for planned business identity and **Technical SAP User** for SAP credentials.
