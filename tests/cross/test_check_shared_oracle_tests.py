"""Regression tests for the shared-oracle test lint."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_shared_oracle_tests.py"
)

MODULE_SOURCE = """
import json

from somelib import supported_params, render
from openhands.sdk.other import local_helper


def get_features(model):
    return _supports(model)


def _supports(model):
    return "effort" in supported_params(model)


def dump(data):
    return json.dumps(data)


def describe(model):
    return local_helper(model)
"""


@pytest.fixture()
def checker(tmp_path: Path) -> Any:
    """The lint, pointed at a tiny repository with one SDK module."""
    sdk = tmp_path / "openhands-sdk" / "openhands" / "sdk"
    sdk.mkdir(parents=True)
    (sdk / "features.py").write_text(MODULE_SOURCE)
    (sdk / "other.py").write_text("def local_helper(model):\n    return model\n")
    (tmp_path / "tests").mkdir()
    spec = importlib.util.spec_from_file_location("checker", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.REPO_ROOT = tmp_path  # type: ignore[attr-defined]
    module.SOURCE_ROOTS = [tmp_path / "openhands-sdk"]  # type: ignore[attr-defined]
    return module


def _check(checker: Any, body: str) -> list[str]:
    path = checker.REPO_ROOT / "tests" / "test_sample.py"
    path.write_text(
        "from somelib import supported_params, render\n"
        "import json\n"
        "from openhands.sdk.features import get_features, dump, describe\n"
        "from openhands.sdk.other import local_helper\n\n" + body
    )
    return checker.check_file(path)


def test_expected_value_from_the_same_dependency_is_reported(checker: Any) -> None:
    errors = _check(
        checker,
        "def test_it():\n"
        "    params = supported_params('m') or []\n"
        "    expected = 'effort' in params\n"
        "    assert get_features('m') == expected\n",
    )

    assert len(errors) == 1
    assert "somelib.supported_params" in errors[0]
    assert "openhands.sdk.features.get_features" in errors[0]


def test_inline_oracle_on_the_left_side_is_reported(checker: Any) -> None:
    errors = _check(
        checker,
        "def test_it():\n"
        "    assert ('effort' in supported_params('m')) == get_features('m')\n",
    )

    assert len(errors) == 1


def test_literal_expected_value_passes(checker: Any) -> None:
    assert (
        _check(checker, "def test_it():\n    assert get_features('m') is True\n") == []
    )


def test_dependency_the_code_does_not_call_passes(checker: Any) -> None:
    errors = _check(
        checker,
        "def test_it():\n    assert get_features('m') == bool(render('m'))\n",
    )

    assert errors == []


def test_standard_library_and_sdk_helpers_are_not_oracles(checker: Any) -> None:
    errors = _check(
        checker,
        "def test_it():\n"
        "    assert dump({'a': 1}) == json.dumps({'a': 1})\n"
        "    assert describe('m') == local_helper('m')\n",
    )

    assert errors == []


def test_suppression_comment_silences_the_report(checker: Any) -> None:
    errors = _check(
        checker,
        "def test_it():\n"
        "    expected = 'effort' in supported_params('m')\n"
        "    assert get_features('m') == expected  # shared-oracle: tracks upstream\n",
    )

    assert errors == []


def test_main_returns_one_when_a_file_is_reported(checker: Any, capsys) -> None:
    path = checker.REPO_ROOT / "tests" / "test_sample.py"
    _check(
        checker,
        "def test_it():\n"
        "    assert get_features('m') == ('effort' in supported_params('m'))\n",
    )

    assert checker.main([str(path)]) == 1
    assert "shared-oracle" in capsys.readouterr().out
