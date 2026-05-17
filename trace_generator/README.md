# ERP Trace Generator

`trace_generator/` turns compiled Pkl configuration YAML into:

- canonical planned execution trace YAML
- post-processing manifest YAML

## Architecture

```mermaid
flowchart LR
  pkl["Pkl configuration\n(configuration/*.pkl)"] --> yaml["Compiled YAML\n(configuration/build/main.yaml)"]
  env["Credentials env\n(configuration/.env)"] --> cli["erp-trace-generate CLI"]
  yaml --> loader["ConfigLoader\nvalidate config, actors, tools, graph"]
  loader --> cases["CaseFactory\nsample cases and master data"]
  cases --> binder["InputBinder\nbind literals, dates, master data, prior outputs"]
  binder --> timeline["TimelinePlanner\nworking hours, pauses, delays"]
  timeline --> scheduler["WaveScheduler\nFIFO, actor locks, technical-user locks"]
  scheduler --> writer["ArtifactWriter"]
  cli --> writer
  writer --> canonical["execution-trace.yaml\ncanonical graph and waves"]
  writer --> manifest["post-processing-manifest.yaml\ncase scenario types, timestamp plan, object lineage, planned date inputs"]
  canonical --> executor["generator/\nSAP execution"]
  canonical --> post["post processor\nplanned truth"]
  manifest --> post
```

Run:

```bash
uv run --project trace_generator erp-trace-generate configuration/build/main.yaml --out-dir trace_generator/build
```

The CLI loads runtime settings from `configuration/.env` by default. Use `--env-file path/to/file.env` when a run needs a different file.

Generated traces do not contain passwords. Canonical session blocks reference env var names so the executor can resolve usernames, passwords, and login URLs at runtime.

With realism enabled, the compiler asks the LLM for compact patterns and models, then expands exact planned cases locally. `llm_metadata` records the criteria hash, schema version, request count, retry count, and cache-hit count.

Goods receipt runtime uses SAP's current posting date. Planned goods-receipt document/posting dates stay in `planned_date_inputs` and `planned_date_input_overrides` so post-processing can rewrite material document exports.
