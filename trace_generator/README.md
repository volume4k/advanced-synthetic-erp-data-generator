# ERP Trace Generator

`trace_generator/` turns compiled Pkl configuration YAML into:

- canonical planned execution trace YAML
- current trace-executor JSONL
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
  writer --> jsonl["executor.trace.jsonl\ncurrent executor bridge"]
  writer --> manifest["post-processing-manifest.yaml\nlabels, timestamp plan, object lineage"]
  jsonl --> executor["generator/\nSAP execution"]
  canonical --> post["post processor\nplanned truth"]
  manifest --> post
```

Run:

```bash
uv run --project trace_generator erp-trace-generate configuration/build/main.yaml --env-file configuration/.env --out-dir trace_generator/build
```

The generated JSONL never contains passwords. Usernames and login URLs are resolved from the env file so the current trace executor can initialize sessions.
