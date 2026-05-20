# ERP Trace Visualizer

Local Bun + Vite app for inspecting generated ERP execution traces and post-processing manifests.

## Run

```bash
bun install
bun run dev
```

Open the printed localhost URL, then upload or paste trace YAML/JSON files.

Production build check:

```bash
bun run build
```

## Supported Inputs

- `*.execution-trace.yaml` / JSON execution trace
- `*.post-processing-manifest.yaml` / JSON post-processing manifest

Files are parsed in the browser. Trace content is not uploaded to a backend.

## Views

- Case Gantt: one row per Process Case with manifest planned timestamps and case material/vendor/quantity facts.
- Wave Matrix: Execution Waves top-to-bottom with Synthetic Actors as columns.
- Actor Calendar: Outlook-style month/week calendar for one Synthetic Actor at a time.
- Graph: Cytoscape dependency graph from `dependency_graph.dependencies`.
- Sessions: technical and virtual user mapping.
- Cases: input cases and line items.
- Manifest: timestamp policy, actor projection, object lineage, expected keys, date overrides, exports, failed case policy.
- Raw: parsed execution trace and manifest JSON.

Time-based views use `planned_step_timestamps` from the Post-Processing Manifest as their source of truth. The Execution Trace joins actor, case, material, vendor, quantity, and price details.

Click any Gantt bar, Wave Matrix card, calendar event, or graph Planned Step to inspect procurement facts, schedule facts, manifest timestamps, inputs, required SAP object keys, labels, dependencies, and matching manifest records.
