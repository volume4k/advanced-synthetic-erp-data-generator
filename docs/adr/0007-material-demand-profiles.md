# Material Demand Profiles With Deterministic Quantity Expansion

The Trace Generator uses **Material Demand Profiles** and **Quantity Profiles** to turn compact LLM intent into exact process-case material assignments and quantities before writing an **Execution Trace**.

## Context

The previous realism model let **Demand Patterns** carry material mix information. That made daily demand responsible for too much: release timing, lead-time behavior, material allocation, and indirectly quantity behavior. It also made small runs sensitive to flat or overly dominant material shares returned by the LLM.

We need material distribution and order quantity realism, but the Trace Executor must stay runtime-only. The Trace Executor should execute planned browser tools from an **Execution Trace**. It should not perform scheduling, material allocation, weight normalization, quantity sampling, or business-date math.

## Decision

- The LLM returns one **Material Demand Profile** for every active configured material.
- Each profile contains a **Relative Demand Weight**, not a probability or final percentage.
- The Trace Generator normalizes those weights into exact material counts for the run horizon.
- The Trace Generator shuffles material assignment with `schedulerSeed` so process cases are not grouped into blocky material runs.
- The LLM returns a **Quantity Profile** for each material: typical order quantity, quantity variation, bulk order share, and order multiple.
- The Trace Generator samples each process-case quantity from the Quantity Profile, rounds to the order multiple, and clamps to configured master-data hard bounds.
- **Demand Patterns** keep only date, case count, workload intensity, release windows, and lead-time mix. They do not own material assignment.
- **Configured Master Data** and **Realism Guardrails** remain the hard source of truth for allowed materials, quantities, weights, variation, bulk share, order multiples, and optional material share caps.
- The Trace Executor consumes only final trace fields: material, vendor, quantity, target price, requested delivery date, planned synthetic time, and human delay profile.

## Consequences

- Large runs scale without asking the LLM to emit per-case material choices.
- The same seed and configuration produce stable material assignment and quantities.
- Missing, duplicate, or unexpected material IDs fail validation and trigger realism retry.
- Small runs can force diversity when `caseCount` is at least the active material count.
- `maxMaterialSharePerHorizon` can prevent one material from dominating a realism-enabled run.
- The Trace Executor boundary stays clean: all planning and realism math happens before execution.
