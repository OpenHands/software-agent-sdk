"""Canonical public OpenAPI document for the Agent Server REST API."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any


PUBLIC_REST_PATH_PREFIX = "/api/"
SCHEMA_REF_PREFIX = "#/components/schemas/"


def _collect_schema_refs(node: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, Mapping):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(SCHEMA_REF_PREFIX):
            refs.add(ref.removeprefix(SCHEMA_REF_PREFIX))
        for value in node.values():
            refs.update(_collect_schema_refs(value))
    elif isinstance(node, list):
        for item in node:
            refs.update(_collect_schema_refs(item))
    return refs


def _prune_unreferenced_schemas(document: dict[str, Any]) -> dict[str, Any]:
    schemas = document.get("components", {}).get("schemas", {})
    if not isinstance(schemas, dict):
        return document

    used: set[str] = set()
    pending = list(_collect_schema_refs(document.get("paths", {})))
    while pending:
        name = pending.pop()
        if name in used or name not in schemas:
            continue
        used.add(name)
        pending.extend(_collect_schema_refs(schemas[name]) - used)

    document.setdefault("components", {})["schemas"] = {
        name: schemas[name] for name in sorted(used)
    }
    return document


def _first_non_null_schema(schema: object) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    variants = schema.get("anyOf")
    if not isinstance(variants, list):
        return schema
    for variant in variants:
        if isinstance(variant, dict) and variant.get("type") != "null":
            return variant
    return None


def _add_canonical_contract_components(document: dict[str, Any]) -> None:
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    if not isinstance(schemas, dict):
        return

    aliases = {
        "MCPServer": "MCPServer-Output",
        "StdioMCPServer": "_StdioMCPServerSpec",
        "RemoteMCPServer": "_RemoteMCPServerSpec",
    }
    for alias, source in aliases.items():
        source_schema = schemas.get(source)
        if alias not in schemas and isinstance(source_schema, dict):
            schemas[alias] = copy.deepcopy(source_schema)
            schemas[alias]["title"] = alias

    mcp_server = schemas.get("MCPServer")
    if isinstance(mcp_server, dict):
        auth = (
            mcp_server.get("properties", {}).get("auth")
            if isinstance(mcp_server.get("properties"), dict)
            else None
        )
        auth_schema = _first_non_null_schema(auth)
        if auth_schema is not None:
            schemas.setdefault(
                "MCPAuthCredential",
                {**copy.deepcopy(auth_schema), "title": "MCPAuthCredential"},
            )

    mcp_test_response = (
        document.get("paths", {})
        .get("/api/mcp/test", {})
        .get("post", {})
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    if isinstance(mcp_test_response, dict):
        schemas.setdefault(
            "MCPTestResponse",
            {**copy.deepcopy(mcp_test_response), "title": "MCPTestResponse"},
        )

    settings_response = schemas.get("SettingsResponse")
    if isinstance(settings_response, dict):
        agent_settings = settings_response.get("properties", {}).get("agent_settings")
        if isinstance(agent_settings, dict):
            schemas.setdefault(
                "AgentSettings",
                {**copy.deepcopy(agent_settings), "title": "AgentSettings"},
            )

    settings_update = schemas.get("SettingsUpdateRequest")
    if isinstance(settings_update, dict):
        agent_settings_diff = settings_update.get("properties", {}).get(
            "agent_settings_diff"
        )
        patch_schema = _first_non_null_schema(agent_settings_diff)
        if patch_schema is not None:
            schemas.setdefault(
                "AgentSettingsPatch",
                {**copy.deepcopy(patch_schema), "title": "AgentSettingsPatch"},
            )

    document["components"]["schemas"] = {
        name: schemas[name] for name in sorted(schemas)
    }


def filter_public_openapi(
    document: dict[str, Any],
    *,
    prune_schemas: bool = True,
    add_contract_components: bool = True,
) -> dict[str, Any]:
    """Return the release contract for the public ``/api`` REST surface."""
    public_document = copy.deepcopy(document)
    public_document["paths"] = {
        path: path_item
        for path, path_item in public_document.get("paths", {}).items()
        if path == PUBLIC_REST_PATH_PREFIX.rstrip("/")
        or path.startswith(PUBLIC_REST_PATH_PREFIX)
    }
    if prune_schemas:
        _prune_unreferenced_schemas(public_document)
    if add_contract_components:
        _add_canonical_contract_components(public_document)
    return public_document


def build_public_openapi() -> dict[str, Any]:
    """Build the exact public OpenAPI document for the current source tree."""
    from openhands.agent_server.api import create_app

    return filter_public_openapi(create_app().openapi())


def serialize_openapi(document: dict[str, Any]) -> str:
    """Serialize OpenAPI deterministically for release and generated clients."""
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
