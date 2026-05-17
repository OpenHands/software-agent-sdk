"""Guard package version changes so they only happen in release PRs."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


PACKAGE_PYPROJECTS: dict[str, Path] = {
    "openhands-sdk": Path("openhands-sdk/pyproject.toml"),
    "openhands-tools": Path("openhands-tools/pyproject.toml"),
    "openhands-workspace": Path("openhands-workspace/pyproject.toml"),
    "openhands-agent-server": Path("openhands-agent-server/pyproject.toml"),
}

_VERSION_PATTERN = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?"
_RELEASE_TITLE_RE = re.compile(rf"^Release v(?P<version>{_VERSION_PATTERN})$")
_RELEASE_BRANCH_RE = re.compile(rf"^rel-(?P<version>{_VERSION_PATTERN})$")


@dataclass(frozen=True)
class VersionChange:
    package: str
    path: Path
    previous_version: str
    current_version: str


def _read_version_from_pyproject_text(text: str, source: str) -> str:
    data = tomllib.loads(text)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str):
        raise SystemExit(f"Unable to determine project.version from {source}")
    return version


def _read_current_version(repo_root: Path, pyproject: Path) -> str:
    return _read_version_from_pyproject_text(
        (repo_root / pyproject).read_text(),
        str(pyproject),
    )


def _read_version_from_git_ref(repo_root: Path, git_ref: str, pyproject: Path) -> str:
    result = subprocess.run(
        ["git", "show", f"{git_ref}:{pyproject.as_posix()}"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise SystemExit(
            f"Unable to read {pyproject} from git ref {git_ref}: {message}"
        )
    return _read_version_from_pyproject_text(result.stdout, f"{git_ref}:{pyproject}")


def _base_ref_candidates(base_ref: str) -> list[str]:
    if base_ref.startswith("origin/"):
        return [base_ref, base_ref.removeprefix("origin/")]
    return [f"origin/{base_ref}", base_ref]


def find_version_changes(repo_root: Path, base_ref: str) -> list[VersionChange]:
    changes: list[VersionChange] = []
    candidates = _base_ref_candidates(base_ref)

    for package, pyproject in PACKAGE_PYPROJECTS.items():
        current_version = _read_current_version(repo_root, pyproject)
        previous_error: SystemExit | None = None
        previous_version: str | None = None

        for candidate in candidates:
            try:
                previous_version = _read_version_from_git_ref(
                    repo_root, candidate, pyproject
                )
                break
            except SystemExit as exc:
                previous_error = exc

        if previous_version is None:
            assert previous_error is not None
            raise previous_error

        if previous_version != current_version:
            changes.append(
                VersionChange(
                    package=package,
                    path=pyproject,
                    previous_version=previous_version,
                    current_version=current_version,
                )
            )

    return changes


def get_release_pr_version(
    pr_title: str, pr_head_ref: str
) -> tuple[str | None, list[str]]:
    title_match = _RELEASE_TITLE_RE.fullmatch(pr_title.strip())
    branch_match = _RELEASE_BRANCH_RE.fullmatch(pr_head_ref.strip())
    title_version = title_match.group("version") if title_match else None
    branch_version = branch_match.group("version") if branch_match else None

    if title_version and branch_version and title_version != branch_version:
        return None, [
            "Release PR markers disagree: title requests "
            f"v{title_version} but branch is rel-{branch_version}."
        ]

    return title_version or branch_version, []


def validate_version_changes(
    changes: list[VersionChange],
    pr_title: str,
    pr_head_ref: str,
) -> list[str]:
    if not changes:
        return []

    release_version, errors = get_release_pr_version(pr_title, pr_head_ref)
    if errors:
        return errors

    formatted_changes = ", ".join(
        f"{change.package} ({change.previous_version} -> {change.current_version})"
        for change in changes
    )

    if release_version is None:
        return [
            "Package version changes are only allowed in release PRs. "
            f"Detected changes: {formatted_changes}. "
            "Use the Prepare Release workflow so the PR title is 'Release vX.Y.Z' "
            "or the branch is 'rel-X.Y.Z'."
        ]

    mismatched = [
        change for change in changes if change.current_version != release_version
    ]
    if mismatched:
        mismatch_details = ", ".join(
            f"{change.package} ({change.current_version})" for change in mismatched
        )
        return [
            f"Release PR version v{release_version} does not match changed package "
            f"versions: {mismatch_details}."
        ]

    return []


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    base_ref = os.environ.get("VERSION_BUMP_BASE_REF") or os.environ.get(
        "GITHUB_BASE_REF"
    )
    if not base_ref:
        print("::warning title=Version bump guard::No base ref found; skipping check.")
        return 0

    pr_title = os.environ.get("PR_TITLE", "")
    pr_head_ref = os.environ.get("PR_HEAD_REF", "")

    changes = find_version_changes(repo_root, base_ref)
    errors = validate_version_changes(changes, pr_title, pr_head_ref)

    if errors:
        for error in errors:
            print(f"::error title=Version bump guard::{error}")
        return 1

    if changes:
        changed_packages = ", ".join(change.package for change in changes)
        print(
            "::notice title=Version bump guard::"
            f"Release PR version changes validated for {changed_packages}."
        )
    else:
        print("::notice title=Version bump guard::No package version changes detected.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
