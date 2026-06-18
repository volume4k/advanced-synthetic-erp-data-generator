# Synthetic ERP Data Generator User Guide

The **Synthetic ERP Data Generator** creates synthetic SAP Fiori ERP process data from planned configuration. It is designed as an end-to-end workflow: configure the process, generate planned artifacts, execute the plan in SAP, export observed SAP data, and produce a final **Synthetic Dataset**.

Use this guide when you want to create or extend a dataset run. Use the component READMEs when you need implementation details for one folder.

## Component Map

| Component | Folder | Responsibility |
|---|---|---|
| Configuration | `configuration/` | Owns Pkl configuration for actors, technical users, master data, process definitions, scenario controls, realism settings, and run settings. |
| Trace Generator | `trace_generator/` | Reads compiled configuration YAML and writes an **Execution Trace** plus **Post-Processing Manifest**. |
| Trace Executor | `trace_executor/` | Runs one Execution Trace against SAP Fiori through registered Browser Tools and writes **Execution Evidence**. |
| Post-Processor | `post_processor/` | Downloads raw SAP exports, joins them with planned artifacts and execution evidence, and writes the final processed dataset. |

## Artifact Flow

```text
configuration/*.pkl
  -> configuration/build/main.yaml
  -> trace_generator/build/<run_id>/<run_id>.execution-trace.yaml
  -> trace_generator/build/<run_id>/<run_id>.post-processing-manifest.yaml
  -> trace_executor/build/<run_id>/<run_id>.execution-log.jsonl
  -> trace_executor/build/<run_id>/<run_id>.object-registry.jsonl
  -> post_processor/downloads/<run_id>/
  -> post_processor/processed/<run_id>/
```

The important contract is:

- The **Execution Trace** is planned truth for process cases, planned steps, actor sessions, tool inputs, and required SAP object keys.
- The **Post-Processing Manifest** is planned truth for reconciliation, timestamp projection, actor projection, labels, and object lineage.
- The **Execution Evidence** records what happened during the SAP execution run.
- The **SAP Exports** contain observed system data from SAP tables.
- The **Synthetic Dataset** is produced only after post-processing applies planned time, synthetic identity, and failure policy.

## Guides

1. [Prerequisites](prerequisites.md): prepare local dependencies, SAP credentials, technical users, and the realism LLM endpoint.
2. [Create a dataset](create-dataset.md): run the complete dataset workflow from configuration through processed output.
3. [Add Browser Tools](add-browser-tools.md): record a SAP action, convert it into a registered Browser Tool, and expose it through configuration.

## Reference Docs

- [Configuration reference](../../configuration/README.md)
- [Trace Generator reference](../../trace_generator/README.md)
- [Trace Executor reference](../../trace_executor/README.md)
- [Post-Processor reference](../../post_processor/README.md)
- [Glossary](../../CONTEXT.md)
- [Architectural decision records](../adr/)
