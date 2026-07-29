"""Tests for updating exact workspace dependency pins during releases."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = (
        repo_root / ".github" / "scripts" / "update_internal_dependency_pins.py"
    )
    name = "update_internal_dependency_pins"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


update_internal_dependency_pins = _load_prod_module().update_internal_dependency_pins


def _write_pyprojects(repo_root: Path) -> None:
    dependencies = {
        "openhands-sdk": [],
        "openhands-tools": [
            "openhands-sdk==1.38.0",
        ],
        "openhands-workspace": [
            "openhands-sdk==1.38.0",
            "openhands-agent-server==1.38.0",
        ],
        "openhands-agent-server": [
            "openhands-sdk==1.38.0",
        ],
    }
    for package, requirements in dependencies.items():
        package_dir = repo_root / package
        package_dir.mkdir()
        rendered = ",\n".join(f'    "{requirement}"' for requirement in requirements)
        (package_dir / "pyproject.toml").write_text(
            f'[project]\nname = "{package}"\ndependencies = [\n{rendered}\n]\n'
        )


def test_updates_selected_workspace_dependency_pins(tmp_path: Path):
    _write_pyprojects(tmp_path)

    changed = update_internal_dependency_pins(
        tmp_path,
        "1.39.0",
        ["openhands-sdk", "openhands-agent-server"],
    )

    assert changed == [
        Path("openhands-tools/pyproject.toml"),
        Path("openhands-workspace/pyproject.toml"),
        Path("openhands-agent-server/pyproject.toml"),
    ]
    assert (
        "openhands-sdk==1.39.0"
        in (tmp_path / "openhands-tools" / "pyproject.toml").read_text()
    )
    workspace = (tmp_path / "openhands-workspace" / "pyproject.toml").read_text()
    assert "openhands-sdk==1.39.0" in workspace
    assert "openhands-agent-server==1.39.0" in workspace


@pytest.mark.parametrize("version", ["latest", "1.2", "1.2.3 nope"])
def test_rejects_invalid_versions(tmp_path: Path, version: str):
    _write_pyprojects(tmp_path)

    with pytest.raises(ValueError, match="Invalid package version"):
        update_internal_dependency_pins(
            tmp_path,
            version,
            ["openhands-sdk"],
        )


def test_rejects_unknown_packages(tmp_path: Path):
    _write_pyprojects(tmp_path)

    with pytest.raises(ValueError, match="Unknown workspace packages"):
        update_internal_dependency_pins(
            tmp_path,
            "1.39.0",
            ["not-a-package"],
        )
