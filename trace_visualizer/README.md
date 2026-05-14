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

- Timeline: one row per case, ordered by planned synthetic time.
- Graph: Cytoscape dependency graph from `dependency_graph.dependencies`.
- Sessions: technical and virtual user mapping.
- Cases: input cases and line items.
- Manifest: timestamp policy, actor projection, object lineage, expected keys, date overrides, exports, failed case policy.
- Raw: parsed execution trace and manifest JSON.

Click any timeline or graph planned step to inspect planned step inputs, required SAP object keys, labels, schedule position, dependencies, and matching manifest records.
