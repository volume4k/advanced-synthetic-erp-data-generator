# Core-Centric Generator Testing

SAP Fiori tool correctness depends on the current tenant, live UI timing, dialogs, data availability, and authorization state, so per-tool mocked SAP click-flow tests create maintenance cost and false confidence. Default automated tests target the generator core: trace parsing and validation, executor ordering and state resolution, registry and tool contracts, `FioriPage`, `FioriMessageHandler`, CLI, credentials, and configuration boundaries.

## Consequences

- Default pytest validates the generator framework rather than pretending to validate live SAP behavior.
- New business tools are covered by generic contracts when registered and backed by password-free example traces.
- Live SAP confidence comes from manual or explicitly marked `live_sap` smoke checks outside the default gate.
