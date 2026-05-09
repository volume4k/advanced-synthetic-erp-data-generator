# 0003. Structured Tool Results For Generated SAP Objects

**Status:** Accepted  
**Date:** 2026-05-09

## Context

Tool results previously exposed useful values as loose flat fields, such as `purchase_requisition` or `purchase_order`. That is convenient for tests, but not precise enough for runtime state, object registry updates, and post-processing.

The generator needs a consistent way to identify which SAP objects were created and which keys can be reused by later tasks.

## Decision

Mutating tools return generated SAP object keys through `data.returned_objects`. They only return keys observed from SAP or deterministically guaranteed by the SAP response.

Example:

```json
{
  "returned_objects": [
    {
      "object_type": "purchase_requisition",
      "keys": {
        "pr_number": "10000030"
      }
    }
  ]
}
```

Flat fields remain for compatibility during the transition.

Tool authors should use the helper instead of hand-writing the nested shape:

```python
returned_object("purchase_order", po_number=purchase_order)
```

## Consequences

- Runtime state updates can use one stable result contract.
- The object registry and post-processor can consume generated keys without scraping human-readable messages.
- Existing tests and examples using flat fields can continue to work.
- Each new mutating tool must declare returned objects in the same structure.
- Item keys such as `pr_item` or `po_item` must not be hard-coded. Add them only after the tool extracts them from SAP.
