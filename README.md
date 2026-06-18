# Advanced Synthetic ERP Data Generator

This repository contains the **Synthetic ERP Data Generator**, a set of tools for creating synthetic SAP Fiori ERP process data for research workflows.

The system plans realistic process cases, executes those planned steps against SAP Fiori, downloads the resulting SAP exports, and post-processes them into a final **Synthetic Dataset** with planned chronology, synthetic actor projection, and provenance.

## User Documentation

Start here:

- [User guide](docs/user-guide/README.md)
- [Prerequisites](docs/user-guide/prerequisites.md)
- [Create a dataset](docs/user-guide/create-dataset.md)
- [Add Browser Tools](docs/user-guide/add-browser-tools.md)

Component-level reference documentation remains in each project folder:

- [Configuration](configuration/README.md)
- [Trace Generator](trace_generator/README.md)
- [Trace Executor](trace_executor/README.md)
- [Post-Processor](post_processor/README.md)

Project glossary terms live in [CONTEXT.md](CONTEXT.md). Architectural decisions live in [docs/adr/](docs/adr/).

## Repository Layout

```text
.
├── configuration/        # Pkl scenario configuration and compiled YAML output
├── trace_generator/      # uv project that creates Execution Trace artifacts
├── trace_executor/       # uv project that executes traces against SAP Fiori
├── post_processor/       # uv project that downloads and processes SAP exports
├── docs/                 # user guide and architectural decision records
├── CONTEXT.md            # project glossary and domain relationships
└── README.md             # project overview
```

## Development

Use conventional commit messages:

```text
feat: add new behavior
fix: patch broken behavior
docs: update documentation
test: add or update tests
```

Use `BREAKING CHANGE:` in the commit footer when a change breaks existing trace formats, APIs, or expected behavior.
