"""Tests for agent-server REST API breakage check script."""

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
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()

_find_deprecation_policy_errors = _prod._find_deprecation_policy_errors
_find_sdk_deprecated_fastapi_routes_in_file = (
    _prod._find_sdk_deprecated_fastapi_routes_in_file
)
_get_baseline_version = _prod._get_baseline_version
_is_minor_or_major_bump = _prod._is_minor_or_major_bump
_normalize_openapi_for_oasdiff = _prod._normalize_openapi_for_oasdiff


def _schema_with_operation(path: str, method: str, operation: dict) -> dict:
    return {
        "openapi": "3.0.0",
        "paths": {
            path: {
                method: operation,
            }
        },
    }


def test_find_deprecation_policy_errors_requires_openapi_deprecated_flag():
    schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "description": (
                "Deprecated since v1.2.3 and scheduled for removal in v1.5.0."
            ),
            "responses": {},
        },
    )

    assert _find_deprecation_policy_errors(schema) == [
        "GET /foo documents deprecation in its description but is not marked "
        "deprecated=true in OpenAPI."
    ]


def test_find_deprecation_policy_errors_accepts_deprecated_operations():
    schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Deprecated since v1.2.3 and scheduled for removal in v1.5.0."
            ),
            "responses": {},
        },
    )

    assert _find_deprecation_policy_errors(schema) == []


def test_find_deprecation_policy_errors_ignores_non_deprecated_operations():
    schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "description": "Current endpoint.",
            "responses": {},
        },
    )

    assert _find_deprecation_policy_errors(schema) == []


def test_find_sdk_deprecated_fastapi_routes_in_file_flags_direct_import(tmp_path):
    repo_root = tmp_path
    source = repo_root / "openhands-agent-server" / "openhands" / "agent_server"
    source.mkdir(parents=True)
    file_path = source / "router.py"
    file_path.write_text(
        "from openhands.sdk.utils.deprecation import deprecated\n"
        "\n"
        '@router.get("/foo")\n'
        '@deprecated(deprecated_in="1.0.0", removed_in="1.1.0")\n'
        "async def foo():\n"
        "    return {}\n"
    )

    errors = _find_sdk_deprecated_fastapi_routes_in_file(file_path, repo_root)

    assert errors == [
        "openhands-agent-server/openhands/agent_server/router.py:5 FastAPI route "
        "`foo` uses openhands.sdk.utils.deprecation.deprecated; use the route "
        "decorator's deprecated=True flag instead."
    ]


def test_find_sdk_deprecated_fastapi_routes_in_file_flags_alias_import(tmp_path):
    repo_root = tmp_path
    source = repo_root / "openhands-agent-server" / "openhands" / "agent_server"
    source.mkdir(parents=True)
    file_path = source / "router.py"
    file_path.write_text(
        "import openhands.sdk.utils.deprecation as dep\n"
        "\n"
        '@router.post("/foo")\n'
        '@dep.deprecated(deprecated_in="1.0.0", removed_in="1.1.0")\n'
        "async def foo():\n"
        "    return {}\n"
    )

    errors = _find_sdk_deprecated_fastapi_routes_in_file(file_path, repo_root)

    assert errors == [
        "openhands-agent-server/openhands/agent_server/router.py:5 FastAPI route "
        "`foo` uses openhands.sdk.utils.deprecation.deprecated; use the route "
        "decorator's deprecated=True flag instead."
    ]


def test_find_sdk_deprecated_fastapi_routes_in_file_ignores_non_route_usage(tmp_path):
    repo_root = tmp_path
    source = repo_root / "openhands-agent-server" / "openhands" / "agent_server"
    source.mkdir(parents=True)
    file_path = source / "helpers.py"
    file_path.write_text(
        "from openhands.sdk.utils.deprecation import deprecated\n"
        "\n"
        '@deprecated(deprecated_in="1.0.0", removed_in="1.1.0")\n'
        "def helper():\n"
        "    return None\n"
    )

    assert _find_sdk_deprecated_fastapi_routes_in_file(file_path, repo_root) == []


def test_get_baseline_version_warns_and_returns_none_when_pypi_fails(
    monkeypatch, capsys
):
    def _raise(_distribution: str) -> dict:  # pragma: no cover
        raise RuntimeError("boom")

    monkeypatch.setattr(_prod, "_fetch_pypi_metadata", _raise)

    assert _get_baseline_version("some-dist", "1.0.0") is None

    captured = capsys.readouterr()
    assert "::warning" in captured.out
    assert "Failed to fetch PyPI metadata" in captured.out


def test_is_minor_or_major_bump():
    assert _is_minor_or_major_bump("1.0.1", "1.0.0") is False
    assert _is_minor_or_major_bump("1.1.0", "1.0.0") is True
    assert _is_minor_or_major_bump("2.0.0", "1.9.9") is True


def test_normalize_openapi_converts_numeric_exclusive_bounds():
    schema = {
        "components": {
            "schemas": {
                "Foo": {
                    "type": "number",
                    "exclusiveMinimum": 3,
                    "exclusiveMaximum": 8,
                },
                "Bar": {
                    "type": "number",
                    "minimum": 0,
                    "exclusiveMinimum": 2,
                },
            }
        },
        "paths": [
            {
                "schema": {
                    "exclusiveMinimum": 1.5,
                }
            }
        ],
    }

    normalized = _normalize_openapi_for_oasdiff(schema)

    foo = normalized["components"]["schemas"]["Foo"]
    assert foo["minimum"] == 3
    assert foo["exclusiveMinimum"] is True
    assert foo["maximum"] == 8
    assert foo["exclusiveMaximum"] is True

    bar = normalized["components"]["schemas"]["Bar"]
    assert bar["minimum"] == 0
    assert bar["exclusiveMinimum"] is True

    assert normalized["paths"][0]["schema"]["minimum"] == 1.5
    assert normalized["paths"][0]["schema"]["exclusiveMinimum"] is True


def test_normalize_openapi_preserves_boolean_exclusive():
    schema = {
        "exclusiveMinimum": True,
        "minimum": 4,
    }

    normalized = _normalize_openapi_for_oasdiff(schema)

    assert normalized["exclusiveMinimum"] is True
    assert normalized["minimum"] == 4
