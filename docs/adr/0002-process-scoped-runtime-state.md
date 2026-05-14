# Process-Scoped Runtime State

Some SAP object identifiers are known only after a tool runs, but traces should stay business-readable instead of carrying verbose task-to-task references. We represent generated SAP object keys in process-scoped runtime state, where a task input beginning with `$` resolves inside the current case, for example `$purchase_requisition.pr_number`.

## Consequences

- Normal procure-to-pay traces can refer to generated values with short `$object.key` variables.
- Missing variables fail before SAP interaction.
- Version one assumes one object of each type per case; future multi-object cases need explicit aliases or item-level scoping.
