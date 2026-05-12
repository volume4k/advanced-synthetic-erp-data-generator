# ERP Trace Generator

`trace_generator/` turns compiled Pkl configuration YAML into:

- canonical planned execution trace YAML
- current trace-executor JSONL
- post-processing manifest YAML

Run:

```bash
uv run --project trace_generator erp-trace-generate configuration/build/main.yaml --env-file configuration/.env --out-dir trace_generator/build
```

The generated JSONL never contains passwords. Usernames and login URLs are resolved from the env file so the current trace executor can initialize sessions.
