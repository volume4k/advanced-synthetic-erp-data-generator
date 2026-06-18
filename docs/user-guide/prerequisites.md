# Prerequisites

Complete these steps before creating a dataset. The commands assume you run them from the repository root.

## Local Tools

Install these tools on the machine that will generate traces, execute SAP browser actions, and run post-processing:

- `uv`
- Python 3.13 managed by `uv`
- `pkl`
- Chromium browser dependencies installed through Playwright

Synchronize the three Python projects:

```bash
uv sync --project trace_generator --python 3.13
uv sync --project trace_executor --python 3.13
uv sync --project post_processor --python 3.13
```

Install Chromium for the projects that use Playwright:

```bash
uv run --project trace_executor playwright install chromium
uv run --project post_processor playwright install chromium
```

## SAP Access

You need access to a reachable SAP Fiori tenant and SAP GUI for HTML / WebGUI transaction `SE16`.

The usual setup is:

- one login URL for the SAP Fiori launchpad
- one or more technical SAP users that can execute the configured Fiori steps
- WebGUI / SE16 authorization for the technical user used by the Post-Processor
- table display access for the SAP exports used by the dataset

The repository stores only environment variable names for SAP accounts. Do not commit usernames, passwords, screenshots with secrets, or traces containing credentials.

## Credential Env File

Create `configuration/.env` locally:

```bash
SAP_URL=<SAP_LOGIN_URL>
SAP_USER_1_UN=<SAP_USERNAME_1>
SAP_USER_1_PW=<SAP_PASSWORD_1>
SAP_USER_2_UN=<SAP_USERNAME_2>
SAP_USER_2_PW=<SAP_PASSWORD_2>
SAP_USER_3_UN=<SAP_USERNAME_3>
SAP_USER_3_PW=<SAP_PASSWORD_3>
SAP_USER_4_UN=<SAP_USERNAME_4>
SAP_USER_4_PW=<SAP_PASSWORD_4>
SAP_USER_5_UN=<SAP_USERNAME_5>
SAP_USER_5_PW=<SAP_PASSWORD_5>
```

`configuration/technical_users.pkl` references these variable names, and `configuration/identity_mapping.pkl` maps each **Synthetic Actor** to a **Technical SAP User**. The Trace Executor resolves the actual values at runtime through `--env-file configuration/.env`.

## Realism LLM Endpoint

When `runSettings.realism.enabled` is `true`, the Trace Generator calls an OpenAI-compatible local LLM endpoint before scheduling. The LLM produces compact realism inputs such as actor baseline models, material demand profiles, quantity profiles, price anchors, and daily demand patterns. The Trace Generator validates those values against configured guardrails and expands exact process cases locally.

Add the endpoint configuration to `configuration/.env` or export it in your shell:

```bash
REALISM_LLM_BASE_URL=http://localhost:1234
REALISM_LLM_MODEL=<model-name>
REALISM_LLM_API_KEY=<optional-token>
```

Shell environment variables take precedence over values in `configuration/.env`.

## Readiness Checks

Check the command entry points:

```bash
uv run --project trace_generator erp-trace-generate --help
uv run --project trace_executor erp-trace-exec --help
uv run --project post_processor erp-sap-export --help
```

Check WebGUI / SE16 access before a long export run:

```bash
uv run --project post_processor erp-sap-export probe \
  --execution-trace trace_generator/build/<run_id>/<run_id>.execution-trace.yaml \
  --env-file configuration/.env
```

Use `--headed` on SAP-facing commands when you need to inspect login, launchpad navigation, or WebGUI behavior.
