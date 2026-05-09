# 0002. Process-Scoped Runtime State

**Status:** Accepted  
**Date:** 2026-05-09

## Context

Some SAP object identifiers are only known after a tool runs. Example: a purchase requisition number must be read from the SAP UI before a later purchase order step can use it.

The trace should stay business-readable. It should not require verbose task-to-task reference paths for normal procure-to-pay cases.

## Decision

Represent generated SAP object keys in a process-scoped runtime state store.

For current JSONL traces, `meta.case_id` is the process scope. A task input string beginning with `$` is a runtime variable resolved inside the current process.

Canonical syntax:

```json
"purchase_requisition": "$purchase_requisition.pr_number"
```

Resolution rule:

```text
current task -> meta.case_id -> objects[purchase_requisition].keys[pr_number]
```

State shape:

```json
{
  "cases": {
    "P2P_C042": {
      "objects": {
        "purchase_requisition": {
          "keys": {
            "pr_number": "10000030"
          },
          "source_task_id": "C042_A1",
          "tool": "fiori.create_purchase_requisition"
        }
      }
    }
  }
}
```

## Consequences

- Normal P2P traces can refer to generated values with short `$object.key` variables.
- The generator can resolve dynamic inputs before Pydantic validation.
- Missing variables fail before SAP interaction.
- Version one assumes one object of each type per process. If a future process creates multiple requisitions or orders, aliases or item-level scoping must be added deliberately.
- Tools only store keys they observed from SAP or can prove from the SAP response. Single-line purchasing does not justify inventing item keys.
