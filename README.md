# Advanced Synthetic ERP Data Generator

This repository contains tools for generating and executing synthetic SAP Fiori ERP process traces. It is part of the SeLLMa research project.

The current implementation focuses on a Pkl-backed trace planner in `trace_generator/` and a Playwright-backed trace executor in `generator/`. The planner writes canonical `execution-trace.yaml` files, and the executor runs those waves against SAP Fiori.

The broader generator vision and planned architecture are documented in `generator_vision_architecture_specification.md`.

## Repository Layout

```text
.
├── generator/       # Independent uv project for trace execution
├── trace_generator/ # Independent uv project for trace planning
├── configuration/   # Scenario/configuration artifacts
├── generator_vision_architecture_specification.md
└── README.md        # Project overview
```

## Component Responsibilities

- `configuration/` owns experiment parameters in Pkl: process steps, tools, actors, technical users, working hours, pause ranges, and delay ranges.
- `trace_generator/` owns planning: case generation, input binding, actor assignment, synthetic timestamps, FIFO wave scheduling, validation, and artifact writing.
- `generator/` owns execution mechanics only: browser sessions, SAP tool calls, runtime placeholder resolution, and SAP object capture.
- Future `post_processor/` work should use the trace-generator execution trace and manifest as planned truth when shifting SAP export timestamps and projecting synthetic actors.

## Quick Start

Bootstrap the trace executor:

```bash
uv sync --project generator --python 3.13
uv run --project generator playwright install chromium
```

Run a trace:

```bash
uv run --project generator erp-trace-exec path/to/execution-trace.yaml
```

Generate trace artifacts from compiled configuration:

```bash
configuration/create-config.sh
uv run --project trace_generator erp-trace-generate configuration/build/main.yaml --out-dir trace_generator/build
```

Run with a visible browser:

```bash
uv run --project generator erp-trace-exec path/to/execution-trace.yaml --headed
```

`uv --project generator` uses `generator/.venv`. If another virtual environment is active, uv may print a warning and ignore it. That is expected.

## Trace Login Flow

Canonical traces contain session blocks with env var names, not credentials:

```yaml
sessions:
- session_id: buyer-session
  virtual_actor_id: buyer-a
  technical_user_id: TU_01
  username_env_var: SAP_USER_1_UN
  password_env_var: SAP_USER_1_PW
  login_url_env_var: SAP_URL
```

Each session is logged in once before scheduled nodes run. Keep real credentials out of Git and put them in `configuration/.env`:

Put credentials in `configuration/.env`:

```bash
SAP_URL=<SAP_LOGIN_URL>
SAP_USER_1_UN=<SAP_USERNAME>
SAP_USER_1_PW=<SAP_PASSWORD>
```

Run the canonical trace:

```bash
uv run --project generator erp-trace-exec trace_generator/build/RUN.execution-trace.yaml --headed
```

The executor resolves usernames, passwords, and login URLs from env vars at runtime. To use a different env file:

```bash
uv run --project generator erp-trace-exec trace_generator/build/RUN.execution-trace.yaml --env-file path/to/credentials.env --headed
```

## Development

Run tests:

```bash
uv run --project generator pytest generator/tests -q
```

The executor is documented in more detail in `generator/README.md`.

External contributors adding browser tools should start with:

- `generator/docs/adding-tools.md`
- `generator/docs/recording-tools.md`
- `generator/docs/locator-guidelines.md`

## Commit Conventions

Use conventional commit messages:

```text
feat: add new behavior
fix: patch broken behavior
docs: update documentation
test: add or update tests
```

Use `BREAKING CHANGE:` in the commit footer when a change breaks existing trace formats, APIs, or expected behavior.
