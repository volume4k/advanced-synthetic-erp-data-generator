# Agent Instructions

After new features or fixes, commit them using conventional commit messages. Be fine granular. Use `BREAKING CHANGE` if applicable. If you encounter problems, fix them immediately.

## Testing Strategy

Default tests for this repository are core-centric. They should prove the generator framework works, not that a mocked SAP page behaves like the real SAP tenant.

- Do not add or maintain per-tool mocked SAP click-flow tests for normal business tool changes.
- New business tools need a registry entry, Pydantic input schema, and password-free example trace so the generic tool contract tests cover them.
- Put shared behavior tests in executor, trace loader, registry, `FioriPage`, or `FioriMessageHandler`.
- Add tool-specific tests only for reusable pure helpers, parsers, formatters, or compact regressions.
- Do not build fake SAP UI clones just to cover locators, popups, tab order, or field sequences.
- Mark automated real SAP checks with `@pytest.mark.live_sap`; those checks are excluded from the default pytest gate.
