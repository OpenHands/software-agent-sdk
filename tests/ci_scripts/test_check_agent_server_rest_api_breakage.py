"""Tests for agent-server REST API breakage check script."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_script_module("check_agent_server_rest_api_breakage")
_deprecations_prod = _load_script_module("check_deprecations")

_find_deprecation_policy_errors = _prod._find_deprecation_policy_errors
_find_sdk_deprecated_fastapi_routes_in_file = (
    _prod._find_sdk_deprecated_fastapi_routes_in_file
)
_get_baseline_version = _prod._get_baseline_version
_normalize_openapi_for_oasdiff = _prod._normalize_openapi_for_oasdiff
_parse_openapi_deprecation_description = _prod._parse_openapi_deprecation_description
_validate_removed_operations = _prod._validate_removed_operations
_rest_route_deprecation_re = _prod.REST_ROUTE_DEPRECATION_RE
_deprecation_check_re = _deprecations_prod.REST_ROUTE_DEPRECATION_RE


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


def test_rest_deprecation_regex_matches_deprecation_check_regex():
    assert _rest_route_deprecation_re.pattern == _deprecation_check_re.pattern
    assert _rest_route_deprecation_re.flags == _deprecation_check_re.flags


def test_parse_openapi_deprecation_description_extracts_versions_from_example():
    description = (
        "Nice description here with more context for API consumers.\n\n"
        " Deprecated since v1.14.0 and scheduled for removal in v1.19.0."
    )

    assert _parse_openapi_deprecation_description(description) == ("1.14.0", "1.19.0")


def test_validate_removed_operations_rejects_malformed_removal_version():
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Nice description here.\n\n"
                " Deprecated since v1.14.0 and scheduled for removal in v1.x.0."
            ),
            "responses": {},
        },
    )

    with pytest.raises(SystemExit, match="Invalid semantic version comparison"):
        _validate_removed_operations(
            [{"path": "/foo", "method": "get", "deprecated": True}],
            prev_schema,
            "1.19.0",
        )


def test_validate_removed_operations_requires_scheduled_removal_version():
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": "Deprecated endpoint.",
            "responses": {},
        },
    )

    errors = _validate_removed_operations(
        [{"path": "/foo", "method": "get", "deprecated": True}],
        prev_schema,
        "1.19.0",
    )

    assert errors == [
        "Removed GET /foo was marked deprecated in the baseline release, but its "
        "OpenAPI description does not declare a scheduled removal version. REST "
        "API removals require 5 minor releases of deprecation runway."
    ]


def test_validate_removed_operations_requires_removal_target_to_be_reached():
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Deprecated since v1.14.0 and scheduled for removal in v1.19.0."
            ),
            "responses": {},
        },
    )

    errors = _validate_removed_operations(
        [{"path": "/foo", "method": "get", "deprecated": True}],
        prev_schema,
        "1.18.0",
    )

    assert errors == [
        "Removed GET /foo before its scheduled removal version v1.19.0 (current "
        "version: v1.18.0). REST API removals require 5 minor releases of "
        "deprecation runway."
    ]


def test_validate_removed_operations_allows_scheduled_removal(capsys):
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Deprecated since v1.14.0 and scheduled for removal in v1.19.0."
            ),
            "responses": {},
        },
    )

    errors = _validate_removed_operations(
        [{"path": "/foo", "method": "get", "deprecated": True}],
        prev_schema,
        "1.19.0",
    )

    assert errors == []
    assert "scheduled removal version v1.19.0" in capsys.readouterr().out


def test_main_allows_scheduled_removal_with_documented_target(monkeypatch, capsys):
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Nice description here.\n\n"
                " Deprecated since v1.9.0 and scheduled for removal in v1.14.0."
            ),
            "responses": {},
        },
    )

    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.14.0")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.13.0"
    )
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: prev_schema
    )
    monkeypatch.setattr(_prod, "_normalize_openapi_for_oasdiff", lambda schema: schema)
    monkeypatch.setattr(
        _prod,
        "_run_oasdiff_breakage_check",
        lambda _prev, _cur: (
            [
                {
                    "id": "removed-operation",
                    "details": {"path": "/foo", "method": "get", "deprecated": True},
                    "text": "removed GET /foo",
                }
            ],
            1,
        ),
    )

    assert _prod.main() == 0

    captured = capsys.readouterr()
    assert "MINOR version bump" not in captured.out
    assert "scheduled removal versions have been reached" in captured.out


def test_main_allows_scheduled_removal_when_baseline_matches_current(
    monkeypatch, capsys
):
    prev_schema = _schema_with_operation(
        "/foo",
        "get",
        {
            "deprecated": True,
            "description": (
                "Nice description here.\n\n"
                " Deprecated since v1.9.0 and scheduled for removal in v1.14.0."
            ),
            "responses": {},
        },
    )

    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.14.0")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.14.0"
    )
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: prev_schema
    )
    monkeypatch.setattr(_prod, "_normalize_openapi_for_oasdiff", lambda schema: schema)
    monkeypatch.setattr(
        _prod,
        "_run_oasdiff_breakage_check",
        lambda _prev, _cur: (
            [
                {
                    "id": "removed-operation",
                    "details": {"path": "/foo", "method": "get", "deprecated": True},
                    "text": "removed GET /foo",
                }
            ],
            1,
        ),
    )

    assert _prod.main() == 0

    captured = capsys.readouterr()
    assert "scheduled removal versions have been reached" in captured.out


def test_main_rejects_breakage_against_pr_base_ref(monkeypatch, capsys):
    monkeypatch.setenv(_prod.BASE_REF_ENV, "main")
    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.16.1")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.16.1"
    )
    monkeypatch.setattr(_prod, "_resolve_git_ref", lambda ref: f"origin/{ref}")
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})

    base_schema = _schema_with_operation("/foo", "get", {"responses": {}})
    monkeypatch.setattr(
        _prod,
        "_generate_openapi_for_git_ref",
        lambda ref: base_schema if ref == "origin/main" else {"paths": {}},
    )

    def _fake_run(prev_spec, _cur_spec):
        prev_schema = json.loads(Path(prev_spec).read_text())
        if "/foo" in prev_schema.get("paths", {}):
            return (
                [
                    {
                        "id": "removed-operation",
                        "details": {
                            "path": "/foo",
                            "method": "get",
                            "deprecated": False,
                        },
                        "text": "removed GET /foo",
                    }
                ],
                1,
            )
        return [], 0

    monkeypatch.setattr(_prod, "_run_oasdiff_breakage_check", _fake_run)

    assert _prod.main() == 1

    captured = capsys.readouterr()
    assert "PR base ref origin/main" in captured.out
    assert "Removed GET /foo without prior deprecation" in captured.out


def test_main_warns_when_pr_base_ref_cannot_be_resolved(monkeypatch, capsys):
    monkeypatch.setenv(_prod.BASE_REF_ENV, "main")
    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.16.1")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.16.1"
    )
    monkeypatch.setattr(_prod, "_resolve_git_ref", lambda _ref: None)
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: {"paths": {}}
    )
    monkeypatch.setattr(
        _prod, "_run_oasdiff_breakage_check", lambda _prev, _cur: ([], 0)
    )

    assert _prod.main() == 0

    captured = capsys.readouterr()
    assert "Unable to resolve base ref 'main'" in captured.out
    assert (
        "No breaking changes detected against baseline release v1.16.1." in captured.out
    )


def test_main_rejects_non_removal_breakage_even_with_newer_version(monkeypatch, capsys):
    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.15.0")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.14.0"
    )
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: {"paths": {}}
    )
    monkeypatch.setattr(_prod, "_normalize_openapi_for_oasdiff", lambda schema: schema)
    monkeypatch.setattr(
        _prod,
        "_run_oasdiff_breakage_check",
        lambda _prev, _cur: (
            [
                {
                    "id": "response-body-changed",
                    "details": {},
                    "text": "response body changed",
                }
            ],
            1,
        ),
    )

    assert _prod.main() == 1

    captured = capsys.readouterr()
    assert "MINOR version bump" not in captured.out
    assert "other than removing previously-deprecated operations" in captured.out


def test_split_breaking_changes_separates_three_buckets():
    changes = [
        {
            "id": "removed-operation",
            "details": {"path": "/foo", "method": "get", "deprecated": True},
            "text": "removed GET /foo",
        },
        {
            "id": "response-property-one-of-added",
            "details": {},
            "text": "added '#/components/schemas/NewTool' to response oneOf",
        },
        {
            "id": "response-body-one-of-added",
            "details": {},
            "text": "added body oneOf member",
        },
        {
            "id": "response-body-any-of-added",
            "details": {},
            "text": "added body anyOf member",
        },
        {
            "id": "response-body-changed",
            "details": {},
            "text": "response body changed",
        },
    ]
    removed, additive_oneof, other = _prod._split_breaking_changes(changes)
    assert len(removed) == 1
    assert removed[0]["path"] == "/foo"
    assert {change["id"] for change in additive_oneof} == {
        "response-property-one-of-added",
        "response-body-one-of-added",
        "response-body-any-of-added",
    }
    assert len(other) == 1
    assert other[0]["id"] == "response-body-changed"


def test_main_passes_when_only_additive_oneof(monkeypatch, capsys):
    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.15.0")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.14.0"
    )
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: {"paths": {}}
    )
    monkeypatch.setattr(_prod, "_normalize_openapi_for_oasdiff", lambda schema: schema)
    monkeypatch.setattr(
        _prod,
        "_run_oasdiff_breakage_check",
        lambda _prev, _cur: (
            [
                {
                    "id": "response-property-one-of-added",
                    "details": {},
                    "text": "added NewTool to response oneOf",
                }
            ],
            1,
        ),
    )

    assert _prod.main() == 0

    captured = capsys.readouterr()
    assert "Additive oneOf/anyOf expansion detected" in captured.out
    assert "additive response oneOf expansions" in captured.out


def test_main_fails_when_additive_oneof_mixed_with_real_breakage(monkeypatch, capsys):
    monkeypatch.setattr(_prod, "_read_version_from_pyproject", lambda _path: "1.15.0")
    monkeypatch.setattr(
        _prod, "_get_baseline_version", lambda _distribution, _current: "1.14.0"
    )
    monkeypatch.setattr(_prod, "_find_sdk_deprecated_fastapi_routes", lambda _root: [])
    monkeypatch.setattr(_prod, "_generate_current_openapi", lambda: {"paths": {}})
    monkeypatch.setattr(_prod, "_find_deprecation_policy_errors", lambda _schema: [])
    monkeypatch.setattr(
        _prod, "_generate_openapi_for_git_ref", lambda _ref: {"paths": {}}
    )
    monkeypatch.setattr(_prod, "_normalize_openapi_for_oasdiff", lambda schema: schema)
    monkeypatch.setattr(
        _prod,
        "_run_oasdiff_breakage_check",
        lambda _prev, _cur: (
            [
                {
                    "id": "response-property-one-of-added",
                    "details": {},
                    "text": "added NewTool to response oneOf",
                },
                {
                    "id": "response-body-changed",
                    "details": {},
                    "text": "response body changed",
                },
            ],
            1,
        ),
    )

    assert _prod.main() == 1

    captured = capsys.readouterr()
    assert "Additive oneOf/anyOf expansion detected" in captured.out
    assert "other than removing previously-deprecated operations" in captured.out


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
