"""Tests for the pre-commit hook that keeps secrets on SecretField."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_secret_fields.py"
PACKAGES = (
    "openhands-sdk",
    "openhands-agent-server",
    "openhands-tools",
    "openhands-workspace",
)


@pytest.fixture(scope="module")
def checker() -> Any:
    spec = importlib.util.spec_from_file_location("check_secret_fields", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("source", "failures"),
    [
        pytest.param("x = 1\n", 0, id="clean"),
        pytest.param(
            "from openhands.sdk.utils.pydantic_secrets import serialize_secret\n",
            1,
            id="imports_helper",
        ),
        pytest.param(
            "import m\nm.validate_secret(v, info)\n",
            1,
            id="helper_by_attribute",
        ),
        pytest.param(
            'model.model_dump(context={"cipher": cipher})\n',
            1,
            id="cipher_dict",
        ),
        pytest.param(
            'ctx = {}\nctx["expose_secrets"] = True\n',
            1,
            id="item_assignment",
        ),
        pytest.param(
            'client.get(url, params={"expose_secrets": "true"})\n',
            0,
            id="http_query_params",
        ),
        pytest.param('data = {"model": "gpt"}\n', 0, id="unrelated_dict"),
    ],
)
def test_checker(checker, tmp_path: Path, source: str, failures: int):
    path = tmp_path / "case.py"
    path.write_text(source)
    assert checker.main([str(path)]) == failures


def test_package_sources_pass(checker):
    files = subprocess.run(
        ["git", "ls-files", "--", *(f"{p}/*.py" for p in PACKAGES)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert files
    assert checker.main([str(REPO_ROOT / f) for f in files]) == 0
