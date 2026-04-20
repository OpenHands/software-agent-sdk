"""Tests for the version bump guard script."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def _load_prod_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / "check_version_bumps.py"
    name = "check_version_bumps"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_prod = _load_prod_module()
VersionChange = _prod.VersionChange
find_version_changes = _prod.find_version_changes
get_release_pr_version = _prod.get_release_pr_version
validate_version_changes = _prod.validate_version_changes


def _write_version(pyproject: Path, version: str) -> None:
    pyproject.write_text(
        f'[project]\nname = "{pyproject.parent.name}"\nversion = "{version}"\n'
    )


def _init_repo_with_versions(tmp_path: Path, version: str) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    for package_dir in (
        "openhands-sdk",
        "openhands-tools",
        "openhands-workspace",
        "openhands-agent-server",
    ):
        package_path = repo_root / package_dir
        package_path.mkdir()
        _write_version(package_path / "pyproject.toml", version)

    subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo_root, check=True)
    subprocess.run(["git", "branch", "origin/main", "HEAD"], cwd=repo_root, check=True)
    return repo_root


def test_get_release_pr_version_accepts_title_or_branch():
    assert get_release_pr_version("Release v1.15.0", "feature/foo") == ("1.15.0", [])
    assert get_release_pr_version("chore: test", "rel-1.15.0") == ("1.15.0", [])


def test_get_release_pr_version_rejects_mismatched_markers():
    version, errors = get_release_pr_version("Release v1.15.0", "rel-1.16.0")

    assert version is None
    assert errors == [
        "Release PR markers disagree: title requests v1.15.0 but branch is rel-1.16.0."
    ]


def test_validate_version_changes_rejects_agent_server_bump_in_non_release_pr():
    changes = [
        VersionChange(
            package="openhands-agent-server",
            path=Path("openhands-agent-server/pyproject.toml"),
            previous_version="1.14.0",
            current_version="1.15.0",
        )
    ]

    errors = validate_version_changes(
        changes,
        pr_title="chore(agent-server): bump version",
        pr_head_ref="fix/agent-server-version-bump",
    )

    assert errors == [
        "Package version changes are only allowed in release PRs. Detected "
        "changes: openhands-agent-server (1.14.0 -> 1.15.0). Use the Prepare "
        "Release workflow so the PR title is 'Release vX.Y.Z' or the branch is "
        "'rel-X.Y.Z'."
    ]


def test_validate_version_changes_accepts_matching_release_version():
    changes = [
        VersionChange(
            package="openhands-agent-server",
            path=Path("openhands-agent-server/pyproject.toml"),
            previous_version="1.14.0",
            current_version="1.15.0",
        )
    ]

    assert (
        validate_version_changes(
            changes,
            pr_title="Release v1.15.0",
            pr_head_ref="rel-1.15.0",
        )
        == []
    )


def test_find_version_changes_detects_agent_server_package(tmp_path: Path):
    repo_root = _init_repo_with_versions(tmp_path, "1.14.0")
    _write_version(
        repo_root / "openhands-agent-server" / "pyproject.toml",
        "1.15.0",
    )

    changes = find_version_changes(repo_root, "main")

    assert changes == [
        VersionChange(
            package="openhands-agent-server",
            path=Path("openhands-agent-server/pyproject.toml"),
            previous_version="1.14.0",
            current_version="1.15.0",
        )
    ]
