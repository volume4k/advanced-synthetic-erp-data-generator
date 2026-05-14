# Structured Tool Results For Generated SAP Objects

Loose flat tool-result fields are convenient for tests, but not precise enough for runtime state, object registry updates, and post-processing. Mutating tools return generated SAP object keys through `data.returned_objects`, using only keys observed from SAP or deterministically guaranteed by the SAP response.

## Consequences

- Runtime state updates, object registry writes, and post-processing consume one stable result contract.
- Flat fields may remain for compatibility during transition.
- Tool authors must not invent item keys such as `pr_item` or `po_item`; add them only after extracting them from SAP.
