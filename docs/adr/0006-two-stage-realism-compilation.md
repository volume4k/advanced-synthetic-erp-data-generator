# Two-Stage Realism Compilation

Realism compilation uses the LLM to produce compact actor, price, and demand models, then the Trace Generator expands those models into exact Process Cases deterministically. This avoids sending or receiving thousands of per-case JSON objects, keeps tests reproducible, and preserves configured guardrails as the hard source of truth for schedule, lead-time, and price values.

## Consequences

- The LLM proposes **Demand Patterns**, **Price Anchors**, and actor baseline models, not concrete high-volume case lists.
- The Trace Generator owns exact Process Case counts, Requested Delivery Dates, release timestamps, and per-case prices.
- Large runs scale with deterministic expansion rather than LLM output size.
