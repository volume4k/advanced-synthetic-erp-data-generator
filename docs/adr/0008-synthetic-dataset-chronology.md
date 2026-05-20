# Synthetic Dataset Chronology Over SAP Physical Write Order

**Status:** accepted
**Date:** 2026-05-20

We use **Planned Synthetic Time** as the chronology source for the final **Synthetic Dataset**, even when SAP physical write order differs during a faster parallel **Execution Run**. This keeps the **Trace Executor** optimized for throughput while the future **Post-Processor** applies **Synthetic Timestamp Projection** to SAP export timestamp fields.

## Consequences

- **Execution Waves** can run independent **Planned Steps** concurrently without trying to force exact SAP save/post order.
- SAP object numbers remain runtime evidence and join identifiers, not the source of dataset chronology.
- The final ML-facing dataset must sort by projected synthetic timestamps, not raw SAP runtime timestamps.
