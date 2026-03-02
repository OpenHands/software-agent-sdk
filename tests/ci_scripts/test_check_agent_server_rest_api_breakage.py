"""Tests for agent-server REST API breakage check script.

We import the production script via a file-based module load so tests remain coupled
to real behavior.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / ".github" / "scripts" / "check_agent_server_rest_api_breakage.py"
    )
    name = "check_agent_server_rest_api_breakage"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register so @dataclass can resolve the module's __dict__
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()

OperationKey = _prod.OperationKey
_compute_breakages = _prod._compute_breakages
_get_previous_version = _prod._get_previous_version
_is_minor_or_major_bump = _prod._is_minor_or_major_bump
_resolve_ref = _prod._resolve_ref


def _schema_with_operation(path: str, method: str, operation: dict) -> dict:
    return {
        "openapi": "3.0.0",
        "paths": {
            path: {
                method: operation,
            }
        },
    }


def test_removed_operation_is_breaking_and_requires_deprecation():
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": False,
            "responses": {},
        },
    )
    current_schema = {"openapi": "3.0.0", "paths": {}}

    reasons, undeprecated = _compute_breakages(prev_schema, current_schema)

    assert any("Removed operations" in reason for reason in reasons)
    assert set(undeprecated) == {OperationKey(method="get", path="/foo")}


def test_removed_deprecated_operation_is_still_breaking_but_allowed():
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "responses": {},
        },
    )
    current_schema = {"openapi": "3.0.0", "paths": {}}

    reasons, undeprecated = _compute_breakages(prev_schema, current_schema)

    assert any("Removed operations" in reason for reason in reasons)
    assert undeprecated == []


def test_new_required_parameter_is_breaking():
    prev_schema = _schema_with_operation("/foo", "get", {"responses": {}})
    current_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "parameters": [
                {
                    "name": "x",
                    "in": "query",
                    "required": True,
                }
            ],
            "responses": {},
        },
    )

    reasons, _undeprecated = _compute_breakages(prev_schema, current_schema)

    assert any("new required params" in reason for reason in reasons)


def test_request_body_becoming_required_is_breaking():
    prev_schema = _schema_with_operation(
        "/foo",
        "post",
        {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                    }
                },
            },
            "responses": {},
        },
    )

    current_schema = _schema_with_operation(
        "/foo",
        "post",
        {
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"type": "object"},
                    }
                },
            },
            "responses": {},
        },
    )

    reasons, _undeprecated = _compute_breakages(prev_schema, current_schema)

    assert any("request body became required" in reason for reason in reasons)


def test_new_required_json_fields_in_request_body_are_breaking():
    prev_schema = _schema_with_operation(
        "/foo",
        "post",
        {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["a"],
                        },
                    }
                },
            },
            "responses": {},
        },
    )

    current_schema = _schema_with_operation(
        "/foo",
        "post",
        {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["a", "b"],
                        },
                    }
                },
            },
            "responses": {},
        },
    )

    reasons, _undeprecated = _compute_breakages(prev_schema, current_schema)

    assert any("new required JSON fields" in reason for reason in reasons)
    assert any("b" in reason for reason in reasons)


def test_resolve_ref_circuit_breaker_handles_cycles():
    spec = {
        "components": {
            "schemas": {
                "A": {"$ref": "#/components/schemas/B"},
                "B": {"$ref": "#/components/schemas/A"},
            }
        }
    }

    resolved = _resolve_ref({"$ref": "#/components/schemas/A"}, spec, max_depth=5)

    assert isinstance(resolved, dict)


def test_get_previous_version_warns_and_returns_none_when_pypi_fails(
    monkeypatch, capsys
):
    def _raise(_distribution: str) -> dict:  # pragma: no cover
        raise RuntimeError("boom")

    monkeypatch.setattr(_prod, "_fetch_pypi_metadata", _raise)

    assert _get_previous_version("some-dist", "1.0.0") is None

    captured = capsys.readouterr()
    assert "::warning" in captured.out
    assert "Failed to fetch PyPI metadata" in captured.out


def test_is_minor_or_major_bump():
    assert _is_minor_or_major_bump("1.0.1", "1.0.0") is False
    assert _is_minor_or_major_bump("1.1.0", "1.0.0") is True
    assert _is_minor_or_major_bump("2.0.0", "1.9.9") is True
