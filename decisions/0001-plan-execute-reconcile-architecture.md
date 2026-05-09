# 0001. Plan-Execute-Reconcile Architecture

**Status:** Accepted  
**Date:** 2026-05-09

## Context

The generator creates synthetic ERP process data by executing configured process instances in an SAP S/4HANA educational system. The system must generate realistic process data, but it must not become an autonomous SAP agent.

Planning, browser execution, and data reconciliation have different responsibilities and failure modes. Mixing them would make traces harder to validate, reproduce, and audit.

## Decision

Use a strict plan-execute-reconcile architecture.

- The trace generator plans cases, dependencies, actor assignment, business dates, labels, and execution waves.
- The generator executes the planned trace through deterministic browser tools.
- The post-processor reconciles the planned trace, execution logs, runtime object registry, and exported SAP data into the final dataset.

The generator must not use an LLM during execution and must not decide which business step comes next.

## Consequences

- Traces can be validated before SAP interaction.
- Browser execution remains deterministic and inspectable.
- Failed cases can be excluded or diagnosed from execution logs and generated object keys.
- Runtime code must reject invalid or incomplete traces instead of repairing them.
