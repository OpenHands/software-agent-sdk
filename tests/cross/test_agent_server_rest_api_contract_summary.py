"""Tests for generated REST API contract PR summaries."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_breakage = _load_script_module("check_agent_server_rest_api_breakage")
_summary = _load_script_module("generate_agent_server_rest_api_contract_summary")
_body = _load_script_module("update_pr_body_with_rest_api_summary")


def _schema(paths: dict, schemas: dict) -> dict:
    return {
        "openapi": "3.1.0",
        "paths": paths,
        "components": {"schemas": schemas},
    }


def test_contract_summary_includes_operation_and_schema_additions():
    previous = _schema(
        {
            "/api/items": {
                "get": {
                    "operationId": "list_items",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            }
                        }
                    },
                }
            }
        },
        {
            "Item": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            }
        },
    )
    current = _schema(
        {
            "/api/items": {
                "get": previous["paths"]["/api/items"]["get"],
                "post": {
                    "operationId": "create_item",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateItem"}
                            }
                        },
                    },
                    "responses": {"204": {"description": "No Content"}},
                },
            }
        },
        {
            "CreateItem": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
            "Item": previous["components"]["schemas"]["Item"],
        },
    )

    diff = _summary.generate_contract_diff(
        previous,
        current,
        previous_label="base",
        current_label="head",
    )

    assert "+operation POST /api/items operationId=create_item" in diff
    assert (
        "+requestBody POST /api/items application/json required=true schema=CreateItem"
    ) in diff
    assert '+schema CreateItem property name required schema=type="string"' in diff


def test_contract_summary_ignores_unreferenced_schema_changes():
    previous = _schema(
        {
            "/api/items": {
                "get": {
                    "operationId": "list_items",
                    "responses": {"204": {"description": "No Content"}},
                }
            }
        },
        {"InternalOnly": {"type": "object", "properties": {"old": {"type": "string"}}}},
    )
    current = _schema(
        previous["paths"],
        {"InternalOnly": {"type": "object", "properties": {"new": {"type": "string"}}}},
    )

    assert (
        _summary.generate_contract_diff(
            previous,
            current,
            previous_label="base",
            current_label="head",
        )
        == ""
    )


def test_contract_summary_shows_schema_property_modifications_as_diff():
    previous = _schema(
        {
            "/api/items": {
                "get": {
                    "operationId": "list_items",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Item"}
                                }
                            }
                        }
                    },
                }
            }
        },
        {
            "Item": {
                "type": "object",
                "properties": {"count": {"type": "integer"}},
            }
        },
    )
    current = _schema(
        previous["paths"],
        {
            "Item": {
                "type": "object",
                "properties": {"count": {"type": "string"}},
            }
        },
    )

    diff = _summary.generate_contract_diff(
        previous,
        current,
        previous_label="base",
        current_label="head",
    )

    assert '-schema Item property count optional schema=type="integer"' in diff
    assert '+schema Item property count optional schema=type="string"' in diff


def test_update_body_inserts_summary_block_inside_agent_summary_section():
    body = """HUMAN:

## Summary

human-written note

---

AGENT:

## Why

Because.

## Summary

- Existing bullet.

## How to Test

pytest
"""

    updated = _body.update_body(
        body, "### REST API contract changes\n\n```diff\n+ GET /api/x\n```"
    )

    agent_summary = updated.rindex("## Summary")
    assert _body.START_MARKER in updated
    assert updated.index(_body.START_MARKER) > agent_summary
    assert updated.index(_body.END_MARKER) < updated.index("## How to Test")
    assert updated.count("human-written note") == 1
    assert "- Existing bullet." in updated


def test_update_body_replaces_or_removes_existing_summary_block():
    body = """AGENT:

## Summary

- Existing bullet.

<!-- agent-server-rest-api-contract-summary:start -->
old
<!-- agent-server-rest-api-contract-summary:end -->

## How to Test

pytest
"""

    replaced = _body.update_body(body, "new")
    assert "old" not in replaced
    assert "new" in replaced

    removed = _body.update_body(replaced, "")
    assert _body.START_MARKER not in removed
    assert "new" not in removed
    assert "- Existing bullet." in removed


def test_update_body_cli_preserves_crlf_when_empty_summary_is_noop(
    tmp_path,
    monkeypatch,
):
    body = (
        "AGENT:\r\n\r\n"
        "## Summary\r\n\r\n"
        "- Existing bullet.\r\n\r\n"
        "## How to Test\r\n\r\n"
        "pytest\r\n"
    )
    body_bytes = body.encode()
    body_file = tmp_path / "body.md"
    summary_file = tmp_path / "summary.md"
    output_file = tmp_path / "output.md"
    body_file.write_bytes(body_bytes)
    summary_file.write_text("")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "update_pr_body_with_rest_api_summary.py",
            "--body-file",
            str(body_file),
            "--summary-file",
            str(summary_file),
            "--output",
            str(output_file),
        ],
    )

    assert _body.main() == 0

    assert output_file.read_bytes() == body_bytes


def test_filter_public_rest_openapi_still_shared_with_breakage_check():
    schema = {"paths": {"/ready": {"get": {}}, "/api/items": {"get": {}}}}

    assert list(_breakage._filter_public_rest_openapi(schema)["paths"]) == [
        "/api/items"
    ]
