# 0004. Core-Centric Generator Testing

**Status:** Accepted  
**Date:** 2026-05-11

## Context

The generator executes deterministic browser tools against a live SAP Fiori system. Individual SAP tools are mostly browser integration scripts, and their correctness depends on the current SAP tenant, live UI timing, dialogs, data availability, and authorization state.

Per-tool mocked SAP click-flow tests create high maintenance cost and false confidence. A fake DOM can prove that a recorded locator sequence was replayed, but it cannot prove that the real SAP workflow still works.

## Decision

Default automated tests target the generator core instead of each business tool's SAP click flow.

The required test surface is:

- trace parsing and validation
- executor ordering, state resolution, result capture, and session behavior
- registry and generic `ToolSpec` contracts
- `FioriPage` wait, retry, settle, and message-recovery behavior
- `FioriMessageHandler` policy, capture, dismiss, fatal handling, and de-dupe behavior
- CLI, credentials, and configuration boundaries

New business tools are covered by generic contract tests when they provide a registered `ToolSpec`, a Pydantic input model, and at least one password-free example trace with valid input.

Tool-specific tests are optional and should be added only for reusable pure logic, parsers, formatters, or compact regressions that cannot be represented better in the shared framework tests.

Live SAP smoke checks are allowed, but they are outside the default pytest gate. Automated live checks must use the `live_sap` pytest marker.

## Consequences

- New SAP tools stay modular and cheap to add, modify, or remove.
- Default pytest validates the generator framework rather than pretending to validate live SAP behavior.
- Real SAP confidence comes from manual or explicitly marked live smoke runs.
- Shared browser and message handling regressions are still protected centrally.
- Tool authors must not add full fake SAP UI clones just to cover new locators, popups, or field sequences.
