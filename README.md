# Advanced Synthetic ERP Data Generator

This repository contains tools for generating and executing synthetic SAP Fiori ERP process traces. It is part of the SeLLMa research project.

The current implementation focuses on a Playwright-backed trace executor in `generator/`. It reads JSONL traces, initializes browser sessions for configured users, logs them into SAP Fiori, then executes tool calls in trace order.

## Repository Layout

```text
.
├── generator/       # Independent uv project for trace execution
├── configuration/   # Scenario/configuration artifacts
├── pyproject.toml   # Root package metadata
└── README.md        # Project overview
```

## Quick Start

Bootstrap the trace executor:

```bash
uv sync --project generator --python 3.13
uv run --project generator playwright install chromium
```

Run a trace:

```bash
uv run --project generator erp-trace-exec path/to/trace.jsonl
```

Run with a visible browser:

```bash
uv run --project generator erp-trace-exec path/to/trace.jsonl --headed
```

`uv --project generator` uses `generator/.venv`. If another virtual environment is active, uv may print a warning and ignore it. That is expected.

## Trace Login Flow

A trace can start with an initialization record that logs in all users once:

```json
{"kind":"init","users":[{"session_id":"buyer-session","user_id":"buyer-a","username":"<SAP_USERNAME>","login_url":"https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"}]}
{"task_id":"task-001","session_id":"buyer-session","user_id":"buyer-a","tool":"fiori.create_order","input":{"item_name":"widget","quantity":3}}
```

Each initialized user gets one browser session. Later task records reuse the same `session_id` and `user_id`; they do not need to repeat credentials.

Keep real credentials out of Git. Use a local temporary trace file for manual login tests:

```bash
cat > /tmp/sap-init-login.trace.jsonl <<'EOF'
{"kind":"init","users":[{"session_id":"tour-user-session","user_id":"tour-user","username":"<SAP_USERNAME>","login_url":"https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204&sap-language=DE"}]}
EOF
```

Put credentials in `configuration/.env`:

```bash
SAP_USER_1_UN=<SAP_USERNAME>
SAP_USER_1_PW=<SAP_PASSWORD>
```

Edit placeholders in `/tmp/sap-init-login.trace.jsonl`, then run:

```bash
uv run --project generator erp-trace-exec /tmp/sap-init-login.trace.jsonl --headed
```

The executor matches the trace username against `*_UN` values in the env file and uses the matching `*_PW` value at runtime. To use a different env file:

```bash
uv run --project generator erp-trace-exec /tmp/sap-init-login.trace.jsonl --env-file path/to/credentials.env --headed
```

## Development

Run tests:

```bash
uv run --project generator pytest generator/tests -q
```

The executor is documented in more detail in `generator/README.md`.

External contributors adding browser tools should start with `generator/docs/adding-tools.md`.

## Commit Conventions

Use conventional commit messages:

```text
feat: add new behavior
fix: patch broken behavior
docs: update documentation
test: add or update tests
```

Use `BREAKING CHANGE:` in the commit footer when a change breaks existing trace formats, APIs, or expected behavior.
