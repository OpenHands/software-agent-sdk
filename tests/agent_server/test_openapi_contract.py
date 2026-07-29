"""Tests for the deterministic public Agent Server OpenAPI contract."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

from openhands.agent_server.openapi import build_public_openapi, serialize_openapi
from openhands.sdk.mcp.config import MCPServer
from openhands.sdk.settings.api_models import MCPServerPatch


def _load_quality_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / ".github" / "scripts" / "check_agent_server_openapi_quality.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_agent_server_openapi_quality",
        script_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_quality = _load_quality_module()


def _schema_ref_name(schema: dict) -> str:
    return schema["$ref"].removeprefix("#/components/schemas/")


def test_public_openapi_export_is_deterministic() -> None:
    first = serialize_openapi(build_public_openapi())
    second = serialize_openapi(build_public_openapi())

    assert first == second
    assert first.endswith("\n")


def test_mcp_contract_has_named_maps_variants_and_discriminators() -> None:
    document = build_public_openapi()
    schemas = document["components"]["schemas"]

    mcp_config = schemas["MCPConfig"]
    assert mcp_config["type"] == "object"
    assert mcp_config["additionalProperties"] == {
        "$ref": "#/components/schemas/MCPServer-Output"
    }
    assert schemas["MCPConfigPatch"]["additionalProperties"]["anyOf"] == [
        {"$ref": "#/components/schemas/MCPServerPatch"},
        {"type": "null"},
    ]
    assert schemas["MCPServer"]["properties"]
    assert schemas["StdioMCPServer"]["properties"]["type"]["const"] == "stdio"
    assert set(schemas["RemoteMCPServer"]["properties"]["type"]["enum"]) == {
        "http",
        "shttp",
        "sse",
        "streamable-http",
    }

    auth = schemas["MCPAuthCredential"]
    assert auth["discriminator"]["propertyName"] == "strategy"
    assert set(auth["discriminator"]["mapping"]) == {
        "api_key",
        "basic",
        "bearer",
        "header",
        "none",
        "oauth2",
    }
    test_server = schemas["MCPTestRequest"]["properties"]["server"]
    assert test_server["discriminator"]["propertyName"] == "type"
    assert schemas["MCPTestResponse"]["discriminator"]["propertyName"] == "ok"


def test_settings_contract_exposes_typed_mcp_response_and_patch() -> None:
    document = build_public_openapi()
    schemas = document["components"]["schemas"]

    response_mcp = schemas["SettingsResponse"]["properties"]["agent_settings"][
        "properties"
    ]["mcp_config"]
    assert _schema_ref_name(response_mcp) == "MCPConfig"

    patch_mcp = schemas["SettingsUpdateRequest"]["properties"]["agent_settings_diff"][
        "anyOf"
    ][0]["properties"]["mcp_config"]["anyOf"][0]
    assert _schema_ref_name(patch_mcp) == "MCPConfigPatch"

    server_patch = schemas["MCPServerPatch"]
    assert "auth" not in server_patch.get("required", [])
    assert {"$ref": "#/components/schemas/MCPServerPatch"} in schemas["MCPConfigPatch"][
        "additionalProperties"
    ]["anyOf"]


def test_mcp_server_crud_operations_use_canonical_contract_types() -> None:
    document = build_public_openapi()
    operations = document["paths"]["/api/settings/mcp/{settings_key}"]

    create = operations["post"]
    assert create["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MCPServer-Input"
    }
    assert create["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SettingsResponse"
    }

    patch = operations["patch"]
    assert patch["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MCPServerPatch"
    }
    assert patch["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SettingsResponse"
    }

    delete = operations["delete"]
    assert "requestBody" not in delete
    assert delete["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SettingsResponse"
    }


def test_mcp_server_patch_tracks_every_canonical_server_field() -> None:
    """Keep the sparse patch contract in lock-step with the persisted model."""
    assert set(MCPServerPatch.model_fields) == set(MCPServer.model_fields)
    assert all(
        not field.is_required() for field in MCPServerPatch.model_fields.values()
    )


def test_weak_schema_detector_finds_recursive_and_missing_schemas() -> None:
    document = {
        "paths": {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "content": {"application/json": {"example": {"name": "x"}}}
                    },
                    "responses": {
                        "200": {
                            "content": {"application/json": {"schema": {}}},
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Opaque": {
                    "type": "object",
                    "properties": {
                        "metadata": {
                            "type": "object",
                            "additionalProperties": True,
                        }
                    },
                }
            }
        },
    }

    findings = _quality.find_weak_locations(document)

    assert (
        _quality.WeakLocation(
            pointer=(
                "/paths/~1api~1items/post/requestBody/content/application~1json/schema"
            ),
            kind="missing-request-schema",
        )
        in findings
    )
    assert (
        _quality.WeakLocation(
            pointer=(
                "/paths/~1api~1items/post/responses/200/content/"
                "application~1json/schema"
            ),
            kind="empty-object-schema",
        )
        in findings
    )
    assert (
        _quality.WeakLocation(
            pointer=(
                "/components/schemas/Opaque/properties/metadata/additionalProperties"
            ),
            kind="unrestricted-additional-properties",
        )
        in findings
    )


def test_weak_schema_allowlist_is_an_exact_ratchet() -> None:
    finding = _quality.WeakLocation(
        pointer="/components/schemas/Opaque/additionalProperties",
        kind="unrestricted-additional-properties",
    )
    matching = _quality.AllowlistEntry(
        pointer=finding.pointer,
        kind=finding.kind,
        reason="Opaque plugin-owned payload.",
        owner="SDK",
    )

    assert _quality.check_allowlist({finding}, [matching]) == []
    assert "new weak schema" in _quality.check_allowlist({finding}, [])[0]
    assert "stale allowlist entry" in _quality.check_allowlist(set(), [matching])[0]

    expired = _quality.AllowlistEntry(
        pointer=finding.pointer,
        kind=finding.kind,
        reason=matching.reason,
        owner=matching.owner,
        expiry=dt.date(2025, 1, 1),
    )
    assert (
        "expired"
        in _quality.check_allowlist(
            {finding},
            [expired],
            today=dt.date(2025, 1, 2),
        )[0]
    )
