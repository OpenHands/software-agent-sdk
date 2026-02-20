#!/usr/bin/env python3
"""Guard against accidental package version bumps on non-release PRs.

Releases are handled via dedicated release PRs (rel-X.Y.Z) created by our
prepare-release workflow. For everyday PRs, package versions in the individual
package ``pyproject.toml`` files should remain unchanged.

This script compares the base and head revisions of a pull request and fails if
any package ``[project].version`` changes.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Package:
    name: str
    pyproject_path: str


PACKAGES: tuple[Package, ...] = (
    Package(name="openhands-sdk", pyproject_path="openhands-sdk/pyproject.toml"),
    Package(name="openhands-tools", pyproject_path="openhands-tools/pyproject.toml"),
    Package(
        name="openhands-workspace",
        pyproject_path="openhands-workspace/pyproject.toml",
    ),
    Package(
        name="openhands-agent-server",
        pyproject_path="openhands-agent-server/pyproject.toml",
    ),
)


def _git_show(sha: str, path: str) -> bytes | None:
    try:
        return subprocess.check_output(
            ["git", "show", f"{sha}:{path}"],
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        sys.stderr.write(
            f"ERROR: Unable to read '{path}' at {sha[:7]}: "
            f"{e.output.decode(errors='replace')}\n"
        )
        return None


def _read_project_version(content: bytes, *, path: str, sha: str) -> str | None:
    try:
        data = tomllib.loads(content.decode())
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"ERROR: Failed parsing TOML for '{path}' at {sha[:7]}: {e}\n")
        return None

    project = data.get("project")
    if not isinstance(project, dict):
        sys.stderr.write(f"ERROR: Missing [project] table in '{path}' at {sha[:7]}\n")
        return None

    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        sys.stderr.write(
            f"ERROR: Missing/invalid [project].version in '{path}' at {sha[:7]}\n"
        )
        return None

    return version.strip()


def main() -> int:
    base_sha = os.environ.get("BASE_SHA")
    head_sha = os.environ.get("HEAD_SHA")

    if not base_sha or not head_sha:
        sys.stderr.write(
            "ERROR: BASE_SHA and HEAD_SHA must be set (pull_request workflow).\n"
        )
        return 2

    failures = 0

    for pkg in PACKAGES:
        base_content = _git_show(base_sha, pkg.pyproject_path)
        head_content = _git_show(head_sha, pkg.pyproject_path)

        if base_content is None or head_content is None:
            failures += 1
            continue

        base_version = _read_project_version(
            base_content,
            path=pkg.pyproject_path,
            sha=base_sha,
        )
        head_version = _read_project_version(
            head_content,
            path=pkg.pyproject_path,
            sha=head_sha,
        )

        if base_version is None or head_version is None:
            failures += 1
            continue

        if base_version == head_version:
            continue

        print(
            f"::error file={pkg.pyproject_path},title=Version bump not allowed::"
            f"{pkg.name} version changed {base_version} -> {head_version}. "
            "Package version bumps must be done in a release PR (rel-X.Y.Z)."
        )
        failures += 1

    if failures:
        print(
            "One or more package versions changed in this PR. "
            "Revert version changes, or use the release PR workflow."
        )
        return 1

    print("No package version bumps detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
