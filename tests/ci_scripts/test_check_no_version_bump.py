"""Tests for the version bump guard script.

We import the production script via a file-based module load so tests stay coupled
to the real CI behavior.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_no_version_bump.py"
    name = "check_no_version_bump"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register so @dataclass can resolve the module's __dict__
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()


def _pyproject_bytes(version: str) -> bytes:
    return (
        f"[project]\nname = 'pkg'\nversion = '{version}'\ndescription = 'x'\n"
    ).encode()


def test_main_requires_pr_shas(monkeypatch):
    monkeypatch.delenv("BASE_SHA", raising=False)
    monkeypatch.delenv("HEAD_SHA", raising=False)

    assert _prod.main() == 2


def test_no_version_bumps_detected(monkeypatch, capsys):
    monkeypatch.setenv("BASE_SHA", "base")
    monkeypatch.setenv("HEAD_SHA", "head")

    def _fake_check_output(cmd, stderr=None):  # noqa: ARG001
        assert cmd[:2] == ["git", "show"]
        sha, path = cmd[2].split(":", 1)
        assert sha in {"base", "head"}
        assert path.endswith("pyproject.toml")
        return _pyproject_bytes("1.2.3")

    monkeypatch.setattr(_prod.subprocess, "check_output", _fake_check_output)

    assert _prod.main() == 0
    out = capsys.readouterr().out
    assert "No package version bumps detected" in out


def test_version_bump_fails(monkeypatch, capsys):
    monkeypatch.setenv("BASE_SHA", "base")
    monkeypatch.setenv("HEAD_SHA", "head")

    def _fake_check_output(cmd, stderr=None):  # noqa: ARG001
        sha, path = cmd[2].split(":", 1)
        if sha == "base":
            return _pyproject_bytes("1.2.3")
        if sha == "head":
            return _pyproject_bytes("1.2.4")
        raise AssertionError(f"Unexpected sha: {sha} ({path})")

    monkeypatch.setattr(_prod.subprocess, "check_output", _fake_check_output)

    assert _prod.main() == 1
    out = capsys.readouterr().out
    assert "Version bump not allowed" in out


def test_malformed_toml_fails(monkeypatch):
    monkeypatch.setenv("BASE_SHA", "base")
    monkeypatch.setenv("HEAD_SHA", "head")

    def _fake_check_output(cmd, stderr=None):  # noqa: ARG001
        sha, _path = cmd[2].split(":", 1)
        if sha == "base":
            return b"[project]\nname = 'x'\nversion = '1.2.3'\n"
        return b"this-is-not-toml:::"

    monkeypatch.setattr(_prod.subprocess, "check_output", _fake_check_output)

    assert _prod.main() == 1


@pytest.mark.parametrize(
    "content",
    [
        b"[project]\nname='x'\nversion='1.2.3'\n",
        b"[project]\nname='x'\nversion='1.2.3'\n",
    ],
)
def test_read_project_version_happy_path(content):
    assert _prod._read_project_version(content, path="p", sha="deadbeef") == "1.2.3"
