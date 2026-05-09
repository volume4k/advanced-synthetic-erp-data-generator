# 0003. Structured Tool Results For Generated SAP Objects

**Status:** Accepted  
**Date:** 2026-05-09

## Context

Tool results previously exposed useful values as loose flat fields, such as `purchase_requisition` or `purchase_order`. That is convenient for tests, but not precise enough for runtime state, object registry updates, and post-processing.

The generator needs a consistent way to identify which SAP objects were created and which keys can be reused by later tasks.

## Decision

Mutating tools return generated SAP object keys through `data.returned_objects`.

Example:

```json
{
  "success": true,
  "returned_objects": [
    {
      "object_type": "purchase_requisition",
      "keys": {
        "pr_number": "10000030",
        "pr_item": "00010"
      }
    }
  ]
}
```

Flat fields remain for compatibility during the transition.

## Consequences

- Runtime state updates can use one stable result contract.
- The object registry and post-processor can consume generated keys without scraping human-readable messages.
- Existing tests and examples using flat fields can continue to work.
- Each new mutating tool must declare returned objects in the same structure.
