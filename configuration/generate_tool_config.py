#!/usr/bin/env python3
"""Generate Pkl tool/process configuration from generator tool inputs."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_SRC = REPO_ROOT / "generator" / "src"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "generated_tool_config.pkl"

if str(GENERATOR_SRC) not in sys.path:
    sys.path.insert(0, str(GENERATOR_SRC))

from erp_trace_executor.registry import build_default_registry  # noqa: E402
from erp_trace_executor.tooling import ToolSpec  # noqa: E402


@dataclass(frozen=True)
class GeneratedObject:
    object_type: str
    required_keys: tuple[str, ...]


@dataclass(frozen=True)
class ToolConfig:
    tool_name: str
    process_type: str
    step_type: str | None
    interface_name: str
    input_model: str
    required_input_fields: tuple[str, ...]
    input_properties: tuple[dict[str, str | bool | None], ...]
    allowed_roles: tuple[str, ...]
    required_sap_app: str | None
    preconditions: tuple[str, ...]
    postconditions: tuple[str, ...]
    generated_objects: tuple[GeneratedObject, ...]
    required_prior_outputs: tuple[str, ...]
    idempotency_policy: str
    parallel_execution_safe: bool
    correlation_fields: tuple[str, ...]


@dataclass(frozen=True)
class ProcessStep:
    step_id: str
    step_type: str
    tool_name: str | None
    required_role: str | None


@dataclass(frozen=True)
class ProcessDependency:
    from_step: str
    to_step: str
    edge_type: str
    reason: str


PROCESS_STEPS: tuple[ProcessStep, ...] = (
    ProcessStep("A1", "create_purchase_requisition", "fiori.create_purchase_requisition", "procurement"),
    ProcessStep("A2", "create_purchase_order", "fiori.create_purchase_order", "procurement"),
    ProcessStep("A3", "release_purchase_order", None, "procurement_manager"),
    ProcessStep("A4", "post_goods_receipt", None, "warehouse"),
    ProcessStep("A5", "enter_incoming_invoice", None, "accounts_payable"),
    ProcessStep("A6", "post_outgoing_payment", None, "accounts_payable"),
)

PROCESS_DEPENDENCIES: tuple[ProcessDependency, ...] = (
    ProcessDependency(
        "create_purchase_requisition",
        "create_purchase_order",
        "data_dependency",
        "Purchase order requires purchase requisition number.",
    ),
    ProcessDependency(
        "create_purchase_order",
        "release_purchase_order",
        "business_dependency",
        "Purchase order must exist before release.",
    ),
    ProcessDependency(
        "release_purchase_order",
        "post_goods_receipt",
        "business_dependency",
        "Goods receipt requires released purchase order.",
    ),
    ProcessDependency(
        "post_goods_receipt",
        "enter_incoming_invoice",
        "business_dependency",
        "Invoice follows goods receipt in version-one P2P.",
    ),
    ProcessDependency(
        "enter_incoming_invoice",
        "post_outgoing_payment",
        "accounting_dependency",
        "Payment requires posted incoming invoice.",
    ),
)

TOOL_OVERRIDES: dict[str, dict[str, Any]] = {
    "fiori.login": {
        "process_type": "runtime",
        "step_type": None,
        "interface_name": "fiori_playwright",
        "allowed_roles": (),
        "required_sap_app": None,
        "preconditions": (),
        "postconditions": ("session_logged_in",),
        "generated_objects": (),
        "required_prior_outputs": (),
        "idempotency_policy": "runtime_session_setup",
        "parallel_execution_safe": True,
        "correlation_fields": ("username",),
    },
    "fiori.create_order": {
        "process_type": "fixture",
        "step_type": "create_order",
        "interface_name": "fixture_fiori_playwright",
        "allowed_roles": ("fixture_user",),
        "required_sap_app": None,
        "preconditions": ("session_logged_in",),
        "postconditions": ("fixture_order_created",),
        "generated_objects": (
            GeneratedObject("fixture_order", ("latest_order", "order_count")),
        ),
        "required_prior_outputs": (),
        "idempotency_policy": "not_idempotent_after_submit",
        "parallel_execution_safe": True,
        "correlation_fields": ("latest_order",),
    },
    "fiori.create_purchase_requisition": {
        "process_type": "procure_to_pay",
        "step_type": "create_purchase_requisition",
        "interface_name": "fiori_playwright",
        "allowed_roles": ("procurement",),
        "required_sap_app": "PurchaseRequisition-create",
        "preconditions": ("material_vendor_combination_valid",),
        "postconditions": ("purchase_requisition_created",),
        "generated_objects": (
            GeneratedObject("purchase_requisition", ("purchase_requisition",)),
        ),
        "required_prior_outputs": (),
        "idempotency_policy": "not_idempotent_after_submit",
        "parallel_execution_safe": True,
        "correlation_fields": ("purchase_requisition",),
    },
    "fiori.create_purchase_order": {
        "process_type": "procure_to_pay",
        "step_type": "create_purchase_order",
        "interface_name": "fiori_playwright",
        "allowed_roles": ("procurement",),
        "required_sap_app": "PurchaseOrder-create",
        "preconditions": ("purchase_requisition_created",),
        "postconditions": ("purchase_order_created",),
        "generated_objects": (
            GeneratedObject("purchase_order", ("purchase_order",)),
        ),
        "required_prior_outputs": ("purchase_requisition.purchase_requisition",),
        "idempotency_policy": "not_idempotent_after_save",
        "parallel_execution_safe": True,
        "correlation_fields": ("purchase_order", "purchase_requisition"),
    },
}


def build_tool_configs() -> tuple[ToolConfig, ...]:
    registry = build_default_registry()
    return tuple(_tool_config(registry.get(name)) for name in sorted(registry.names()))


def render_pkl(tool_configs: tuple[ToolConfig, ...]) -> str:
    lines = [
        "// @generated by configuration/generate_tool_config.py",
        "// Do not edit manually.",
        "",
        'import "tool_config_schema.pkl"',
        "",
        "tools: Mapping<String, tool_config_schema.ToolRequirement> = new Mapping {",
    ]
    for tool in tool_configs:
        lines.extend(_render_tool(tool, indent=2))
    lines.extend(
        [
            "}",
            "",
            "procureToPayProcess: tool_config_schema.ProcureToPayProcess = new tool_config_schema.ProcureToPayProcess {",
            '  processType = "procure_to_pay"',
            "  steps {",
        ]
    )
    for step in PROCESS_STEPS:
        lines.extend(_render_step(step, indent=4))
    lines.extend(["  }", "  dependencies {"])
    for dependency in PROCESS_DEPENDENCIES:
        lines.extend(_render_dependency(dependency, indent=4))
    lines.extend(["  }", "}", ""])
    return "\n".join(lines)


def write_generated_config(output_path: Path) -> None:
    output_path.write_text(render_pkl(build_tool_configs()), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    write_generated_config(args.output)
    return 0


def _tool_config(spec: ToolSpec) -> ToolConfig:
    overrides = TOOL_OVERRIDES.get(spec.name)
    if overrides is None:
        raise ValueError(f"Missing configuration-owned metadata for tool: {spec.name}")

    schema = spec.input_model.model_json_schema()
    properties = schema.get("properties", {})
    required_fields = tuple(schema.get("required", ()))
    input_properties = tuple(
        {
            "name": name,
            "schema_type": _schema_type(prop_schema),
            "required": name in required_fields,
            "default_value": _default_value(prop_schema),
        }
        for name, prop_schema in properties.items()
    )

    return ToolConfig(
        tool_name=spec.name,
        process_type=overrides["process_type"],
        step_type=overrides["step_type"],
        interface_name=overrides["interface_name"],
        input_model=spec.input_model.__name__,
        required_input_fields=required_fields,
        input_properties=input_properties,
        allowed_roles=overrides["allowed_roles"],
        required_sap_app=overrides["required_sap_app"],
        preconditions=overrides["preconditions"],
        postconditions=overrides["postconditions"],
        generated_objects=overrides["generated_objects"],
        required_prior_outputs=overrides["required_prior_outputs"],
        idempotency_policy=overrides["idempotency_policy"],
        parallel_execution_safe=overrides["parallel_execution_safe"],
        correlation_fields=overrides["correlation_fields"],
    )


def _schema_type(schema: dict[str, Any]) -> str:
    if "type" in schema:
        return str(schema["type"])
    if "anyOf" in schema:
        return " | ".join(_schema_type(item) for item in schema["anyOf"])
    return "unknown"


def _default_value(schema: dict[str, Any]) -> str | None:
    if "default" not in schema:
        return None
    if schema["default"] is None:
        return None
    return str(schema["default"])


def _render_tool(tool: ToolConfig, *, indent: int) -> list[str]:
    pad = " " * indent
    lines = [
        f'{pad}["{_escape(tool.tool_name)}"] = new tool_config_schema.ToolRequirement {{',
        f'{pad}  toolName = "{_escape(tool.tool_name)}"',
        f'{pad}  toolVersion = "0.1.0"',
        f'{pad}  processType = "{_escape(tool.process_type)}"',
        f"{pad}  stepType = {_pkl_optional_string(tool.step_type)}",
        f'{pad}  interfaceName = "{_escape(tool.interface_name)}"',
        f'{pad}  inputModel = "{_escape(tool.input_model)}"',
        f"{pad}  requiredInputFields {{",
    ]
    lines.extend(_render_string_elements(tool.required_input_fields, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  inputProperties {{")
    for prop in tool.input_properties:
        lines.extend(_render_input_property(prop, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  allowedRoles {{")
    lines.extend(_render_string_elements(tool.allowed_roles, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  requiredSapApp = {_pkl_optional_string(tool.required_sap_app)}")
    lines.append(f"{pad}  preconditions {{")
    lines.extend(_render_string_elements(tool.preconditions, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  postconditions {{")
    lines.extend(_render_string_elements(tool.postconditions, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  generatedObjects {{")
    for generated_object in tool.generated_objects:
        lines.extend(_render_generated_object(generated_object, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}  requiredPriorOutputs {{")
    lines.extend(_render_string_elements(tool.required_prior_outputs, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f'{pad}  idempotencyPolicy = "{_escape(tool.idempotency_policy)}"')
    lines.append(f"{pad}  parallelExecutionSafe = {str(tool.parallel_execution_safe).lower()}")
    lines.append(f"{pad}  correlationFields {{")
    lines.extend(_render_string_elements(tool.correlation_fields, indent=indent + 4))
    lines.append(f"{pad}  }}")
    lines.append(f"{pad}}}")
    return lines


def _render_input_property(prop: dict[str, str | bool | None], *, indent: int) -> list[str]:
    pad = " " * indent
    return [
        f"{pad}new tool_config_schema.InputProperty {{",
        f'{pad}  name = "{_escape(str(prop["name"]))}"',
        f'{pad}  schemaType = "{_escape(str(prop["schema_type"]))}"',
        f"{pad}  required = {str(prop['required']).lower()}",
        f"{pad}  defaultValue = {_pkl_optional_string(prop['default_value'])}",
        f"{pad}}}",
    ]


def _render_generated_object(generated_object: GeneratedObject, *, indent: int) -> list[str]:
    pad = " " * indent
    lines = [
        f"{pad}new tool_config_schema.GeneratedObject {{",
        f'{pad}  objectType = "{_escape(generated_object.object_type)}"',
        f"{pad}  requiredKeys {{",
    ]
    lines.extend(_render_string_elements(generated_object.required_keys, indent=indent + 4))
    lines.extend([f"{pad}  }}", f"{pad}}}"])
    return lines


def _render_step(step: ProcessStep, *, indent: int) -> list[str]:
    pad = " " * indent
    return [
        f"{pad}new tool_config_schema.ProcessStep {{",
        f'{pad}  stepId = "{_escape(step.step_id)}"',
        f'{pad}  stepType = "{_escape(step.step_type)}"',
        f"{pad}  toolName = {_pkl_optional_string(step.tool_name)}",
        f"{pad}  requiredRole = {_pkl_optional_string(step.required_role)}",
        f"{pad}}}",
    ]


def _render_dependency(dependency: ProcessDependency, *, indent: int) -> list[str]:
    pad = " " * indent
    return [
        f"{pad}new tool_config_schema.ProcessDependency {{",
        f'{pad}  fromStep = "{_escape(dependency.from_step)}"',
        f'{pad}  toStep = "{_escape(dependency.to_step)}"',
        f'{pad}  edgeType = "{_escape(dependency.edge_type)}"',
        f'{pad}  reason = "{_escape(dependency.reason)}"',
        f"{pad}}}",
    ]


def _render_string_elements(values: tuple[str, ...], *, indent: int) -> list[str]:
    pad = " " * indent
    return [f'{pad}"{_escape(value)}"' for value in values]


def _pkl_optional_string(value: str | bool | None) -> str:
    if value is None:
        return "null"
    return f'"{_escape(str(value))}"'


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


if __name__ == "__main__":
    raise SystemExit(main())
