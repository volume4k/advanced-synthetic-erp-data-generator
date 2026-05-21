# Post-Processor Requirements

This folder records requirements for the future Post-Processor. No runtime implementation lives here yet.

## Chronology Contract

- The final ML-facing **Synthetic Dataset** uses **Planned Synthetic Time** as chronology truth.
- SAP export timestamp fields must be replaced through **Synthetic Timestamp Projection**.
- SAP physical write order may differ from final dataset order.
- Raw SAP runtime timestamps may be kept only in provenance or debug output, not as ML-facing chronology fields.
- SAP document numbers and object keys stay unchanged and remain join identifiers.

## Inputs

- **Execution Trace**: process cases, planned steps, planned synthetic time, actor sessions, inputs, labels, and required SAP object keys.
- **Post-Processing Manifest**: timestamp policy, planned step timestamps, actor projection, object lineage, planned date input overrides, and failed-case policy.
- **Execution Evidence**: execution log and object registry from the Trace Executor.
- **SAP Exports**: table extracts such as purchase orders, material documents, supplier invoices, accounting documents, CDHDR, and CDPOS.

## Required Behavior

- Exclude **Failed Process Cases** using Execution Evidence and Object Registry, not timestamp windows alone.
- Join SAP export rows to **Process Cases** through SAP object keys from the Object Registry.
- Replace table-specific date/time columns with the matching planned synthetic timestamp for the associated **Planned Step**.
- Preserve within-case process order from the Execution Trace.
- Preserve original technical SAP users in provenance output.
- Project synthetic actor identity according to actor projection rules.
- Keep planned business dates distinct from technical timestamps.

## Open Implementation Decisions

- Define table-specific timestamp fields and key fields for each SAP export.
- Define provenance table/file shape for raw SAP timestamps and technical SAP users.
- Define how to handle SAP exports where one row combines data from multiple planned steps.
- Define validation reports for missing object-registry joins, missing timestamp projections, and order violations.
