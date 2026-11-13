"""Regression tests for the execution_status write guard pre-commit hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_execution_status_writes.py"
)


@pytest.fixture(scope="module")
def checker() -> Any:
    spec = importlib.util.spec_from_file_location("status_checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        pytest.param("state.execution_status = s\n", 1, id="assign"),
        pytest.param("a = state.execution_status = s\n", 1, id="chained_assign"),
        pytest.param("state.execution_status: S = s\n", 1, id="annotated"),
        pytest.param('setattr(state, "execution_status", s)\n', 1, id="setattr"),
        pytest.param(
            "state.execution_status = (\n    s\n)\n", 1, id="multiline_assign"
        ),
        pytest.param('state.transition(s, "why")\n', 0, id="transition"),
        pytest.param("ok = state.execution_status == s\n", 0, id="comparison"),
        pytest.param("info = Info(execution_status=s)\n", 0, id="keyword"),
        pytest.param("execution_status = s\n", 0, id="local_name"),
        pytest.param(
            "record.execution_status = s  # status-write: mirror\n",
            0,
            id="marked_same_line",
        ),
        pytest.param(
            "# status-write: mirror\nrecord.execution_status = s\n",
            0,
            id="marked_line_above",
        ),
    ],
)
def test_guard(checker, tmp_path: Path, source: str, expected: int):
    path = tmp_path / "case.py"
    path.write_text(source)
    assert checker.main([str(path)]) == expected


def test_package_sources_pass(checker):
    assert checker.main([]) == 0
