# Plan-Execute-Reconcile Architecture

The generator creates synthetic ERP process data by executing configured process instances in an SAP S/4HANA educational system, but it must not become an autonomous SAP agent. We use a strict plan-execute-reconcile architecture: the trace generator plans cases, dependencies, actor assignment, business dates, labels, and execution waves; the trace executor runs the planned trace through deterministic browser tools; and the post-processor reconciles planned trace data, execution logs, runtime object registry entries, and exported SAP data.

## Consequences

- Traces can be validated before SAP interaction.
- Browser execution remains deterministic and inspectable.
- Runtime code rejects invalid or incomplete traces instead of repairing them.
