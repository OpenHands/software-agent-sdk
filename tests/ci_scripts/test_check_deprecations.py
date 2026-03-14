"""Tests for deprecation deadline script."""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_deprecations.py"
    name = "check_deprecations"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
DeprecationRecord = _prod.DeprecationRecord
_gather_rest_route_deprecations = _prod._gather_rest_route_deprecations
_should_fail = _prod._should_fail


def test_gather_rest_route_deprecations_collects_deprecated_route(tmp_path):
    path = tmp_path / "router.py"
    tree = ast.parse(
        '@router.post("/foo", deprecated=True)\n'
        "async def foo():\n"
        '    """Deprecated since v1.11.5 and scheduled for removal in v1.14.0."""\n'
        "    return {}\n"
    )

    records = list(
        _gather_rest_route_deprecations(
            tree,
            path,
            package="openhands-agent-server",
        )
    )

    assert len(records) == 1
    record = records[0]
    assert record.identifier == "POST /foo"
    assert record.deprecated_in == "1.11.5"
    assert record.removed_in == "1.14.0"
    assert record.kind == "rest_route"
    assert record.path == path


def test_gather_rest_route_deprecations_supports_api_route_methods(tmp_path):
    path = tmp_path / "router.py"
    tree = ast.parse(
        '@router.api_route("/foo", methods=["POST", "DELETE"], deprecated=True)\n'
        "async def foo():\n"
        '    """Deprecated since v1.15.0 and scheduled for removal in v1.20.0."""\n'
        "    return {}\n"
    )

    records = list(
        _gather_rest_route_deprecations(
            tree,
            path,
            package="openhands-agent-server",
        )
    )

    assert {record.identifier for record in records} == {"POST /foo", "DELETE /foo"}


def test_gather_rest_route_deprecations_ignores_non_deprecated_routes(tmp_path):
    path = tmp_path / "router.py"
    tree = ast.parse('@router.get("/foo")\nasync def foo():\n    return {}\n')

    assert (
        list(
            _gather_rest_route_deprecations(
                tree,
                path,
                package="openhands-agent-server",
            )
        )
        == []
    )


def test_gather_rest_route_deprecations_requires_parseable_docstring(tmp_path):
    path = tmp_path / "router.py"
    tree = ast.parse(
        '@router.get("/foo", deprecated=True)\n'
        "async def foo():\n"
        '    """Deprecated endpoint."""\n'
        "    return {}\n"
    )

    with pytest.raises(SystemExit, match="Deprecated REST route"):
        list(
            _gather_rest_route_deprecations(
                tree,
                path,
                package="openhands-agent-server",
            )
        )


def test_should_fail_for_overdue_rest_route_record():
    record = DeprecationRecord(
        identifier="POST /foo",
        removed_in="1.14.0",
        deprecated_in="1.11.5",
        path=Path("router.py"),
        line=10,
        kind="rest_route",
        package="openhands-agent-server",
    )

    assert _should_fail("1.14.0", record) is True
    assert _should_fail("1.13.9", record) is False
