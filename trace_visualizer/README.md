# ERP Trace Visualizer

Local Bun + Vite app for inspecting generated ERP execution traces and post-processing manifests.

## Run

```bash
bun install
bun run dev
```

Open the printed localhost URL, then upload or paste trace YAML/JSON files.

## Supported Inputs

- `*.execution-trace.yaml` / JSON execution trace
- `*.post-processing-manifest.yaml` / JSON post-processing manifest

Files are parsed in the browser. Trace content is not uploaded to a backend.
