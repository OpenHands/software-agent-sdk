#!/usr/bin/env python3
"""Generate a concise public REST API contract diff for PR descriptions."""

from __future__ import annotations

import argparse
import difflib
import json
import re
from pathlib import Path
from typing import Any

import check_agent_server_rest_api_breakage as rest_api


DEFAULT_MAX_DIFF_LINES = 120
SCHEMA_REF_RE = re.compile(r"^#/components/schemas/(?P<name>[^/]+)$")
SCHEMA_CONSTRAINT_KEYS = (
    "format",
    "enum",
    "const",
    "default",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
)


def _copy_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(schema))


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _schema_ref_name(ref: str) -> str | None:
    match = SCHEMA_REF_RE.match(ref)
    return match.group("name") if match else None


def _collect_schema_refs(node: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and (name := _schema_ref_name(ref)):
            refs.add(name)
        for value in node.values():
            refs.update(_collect_schema_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.update(_collect_schema_refs(item))
    return refs


def _prune_unreferenced_schemas(schema: dict[str, Any]) -> dict[str, Any]:
    schema = _copy_schema(schema)
    schemas = schema.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict):
        return schema

    used: set[str] = set()
    pending = list(_collect_schema_refs(schema.get("paths", {})))
    while pending:
        name = pending.pop()
        if name in used or name not in schemas:
            continue
        used.add(name)
        pending.extend(_collect_schema_refs(schemas[name]) - used)

    schema.setdefault("components", {})["schemas"] = {
        name: schemas[name] for name in sorted(used)
    }
    return schema


def _schema_signature(schema: Any) -> str:
    if not isinstance(schema, dict):
        return _compact(schema)

    ref = schema.get("$ref")
    if isinstance(ref, str) and (name := _schema_ref_name(ref)):
        return name

    parts: list[str] = []
    schema_type = schema.get("type")
    if schema_type is not None:
        parts.append(f"type={_compact(schema_type)}")

    for union_key in ("oneOf", "anyOf", "allOf"):
        values = schema.get(union_key)
        if isinstance(values, list):
            union_values = ",".join(_schema_signature(value) for value in values)
            parts.append(f"{union_key}=[{union_values}]")

    items = schema.get("items")
    if items is not None:
        parts.append(f"items={_schema_signature(items)}")

    additional_properties = schema.get("additionalProperties")
    if additional_properties is not None:
        if isinstance(additional_properties, dict):
            value = _schema_signature(additional_properties)
        else:
            value = _compact(additional_properties)
        parts.append(f"additionalProperties={value}")

    for key in SCHEMA_CONSTRAINT_KEYS:
        if key in schema:
            parts.append(f"{key}={_compact(schema[key])}")

    if not parts and "properties" in schema:
        parts.append("type=object")

    return " ".join(parts) if parts else "{}"


def _iter_media_schemas(content: Any):
    if not isinstance(content, dict):
        return
    for media_type, media_object in sorted(content.items()):
        if not isinstance(media_object, dict):
            continue
        yield media_type, media_object.get("schema", {})


def _operation_contract_lines(
    path: str, method: str, operation: dict[str, Any]
) -> list[str]:
    label = f"{method.upper()} {path}"
    details: list[str] = []
    if operation_id := operation.get("operationId"):
        details.append(f"operationId={operation_id}")
    if operation.get("deprecated") is True:
        details.append("deprecated=true")

    suffix = f" {' '.join(details)}" if details else ""
    lines = [f"operation {label}{suffix}"]

    parameters = operation.get("parameters", [])
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name", "<unknown>")
            location = parameter.get("in", "<unknown>")
            required = str(parameter.get("required") is True).lower()
            signature = _schema_signature(parameter.get("schema", {}))
            lines.append(
                f"parameter {label} {location}:{name} "
                f"required={required} schema={signature}"
            )

    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        required = str(request_body.get("required") is True).lower()
        for media_type, schema in _iter_media_schemas(request_body.get("content")):
            lines.append(
                f"requestBody {label} {media_type} "
                f"required={required} schema={_schema_signature(schema)}"
            )

    responses = operation.get("responses", {})
    if isinstance(responses, dict):
        for status_code, response in sorted(responses.items()):
            if not isinstance(response, dict):
                continue
            media_schemas = list(_iter_media_schemas(response.get("content")))
            if not media_schemas:
                lines.append(f"response {label} {status_code} no-content")
                continue
            for media_type, schema in media_schemas:
                lines.append(
                    f"response {label} {status_code} {media_type} "
                    f"schema={_schema_signature(schema)}"
                )

    return lines


def _schema_contract_lines(name: str, schema: dict[str, Any]) -> list[str]:
    schema_without_properties = {
        key: value
        for key, value in schema.items()
        if key not in {"properties", "required", "title", "description"}
    }
    lines = [f"schema {name} {_schema_signature(schema_without_properties)}"]

    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return lines

    for property_name, property_schema in sorted(properties.items()):
        requirement = "required" if property_name in required else "optional"
        lines.append(
            f"schema {name} property {property_name} {requirement} "
            f"schema={_schema_signature(property_schema)}"
        )

    return lines


def _flatten_public_contract(schema: dict[str, Any]) -> list[str]:
    schema = _prune_unreferenced_schemas(schema)
    lines: list[str] = []

    paths = schema.get("paths", {})
    if isinstance(paths, dict):
        for path, path_item in sorted(paths.items()):
            if not isinstance(path_item, dict):
                continue
            for method, operation in sorted(path_item.items()):
                if method not in rest_api.HTTP_METHODS or not isinstance(
                    operation, dict
                ):
                    continue
                lines.extend(_operation_contract_lines(path, method, operation))

    schemas = schema.get("components", {}).get("schemas", {})
    if isinstance(schemas, dict):
        for name, component_schema in sorted(schemas.items()):
            if isinstance(component_schema, dict):
                lines.extend(_schema_contract_lines(name, component_schema))

    return sorted(set(lines))


def generate_contract_diff(
    previous_schema: dict[str, Any],
    current_schema: dict[str, Any],
    *,
    previous_label: str,
    current_label: str,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> str:
    previous_lines = _flatten_public_contract(previous_schema)
    current_lines = _flatten_public_contract(current_schema)
    if previous_lines == current_lines:
        return ""

    diff_lines = list(
        difflib.unified_diff(
            previous_lines,
            current_lines,
            fromfile=previous_label,
            tofile=current_label,
            n=0,
            lineterm="",
        )
    )
    if len(diff_lines) > max_diff_lines:
        omitted = len(diff_lines) - max_diff_lines
        diff_lines = diff_lines[:max_diff_lines]
        diff_lines.append(f"... diff truncated; {omitted} more line(s)")

    return "\n".join(diff_lines)


def generate_contract_summary(
    previous_schema: dict[str, Any],
    current_schema: dict[str, Any],
    *,
    base_ref: str,
    max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
) -> str:
    diff = generate_contract_diff(
        previous_schema,
        current_schema,
        previous_label="base public OpenAPI",
        current_label="head public OpenAPI",
        max_diff_lines=max_diff_lines,
    )
    if not diff:
        return ""

    short_ref = base_ref[:12]
    return (
        "### REST API contract changes\n\n"
        f"Compared with base OpenAPI `{short_ref}` for public `/api/**` paths.\n\n"
        "```diff\n"
        f"{diff}\n"
        "```\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a PR-description summary for REST API contract changes."
    )
    parser.add_argument("--base-ref", required=True, help="Git ref to compare against.")
    parser.add_argument(
        "--output", type=Path, required=True, help="Markdown output path."
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=DEFAULT_MAX_DIFF_LINES,
        help="Maximum diff lines to include in the PR description.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    previous_schema = rest_api._generate_openapi_for_git_ref(args.base_ref)
    current_schema = rest_api._generate_current_openapi()
    if previous_schema is None or current_schema is None:
        args.output.write_text("")
        return 0

    previous_schema = rest_api._filter_public_rest_openapi(previous_schema)
    current_schema = rest_api._filter_public_rest_openapi(current_schema)
    summary = generate_contract_summary(
        previous_schema,
        current_schema,
        base_ref=args.base_ref,
        max_diff_lines=args.max_diff_lines,
    )
    args.output.write_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
