"""Enforce exact versions for direct workspace dependency declarations."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path

from packaging.requirements import Requirement


REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PYPROJECTS = (
    REPO_ROOT / "openhands-sdk" / "pyproject.toml",
    REPO_ROOT / "openhands-tools" / "pyproject.toml",
    REPO_ROOT / "openhands-workspace" / "pyproject.toml",
    REPO_ROOT / "openhands-agent-server" / "pyproject.toml",
)


def _root_requirements(data: dict) -> Iterable[tuple[str, str]]:
    for requirement in data["tool"]["uv"]["constraint-dependencies"]:
        yield "tool.uv.constraint-dependencies", requirement
    for group, requirements in data["dependency-groups"].items():
        for requirement in requirements:
            yield f"dependency-groups.{group}", requirement


def _package_requirements(path: Path, data: dict) -> Iterable[tuple[str, str]]:
    for requirement in data["project"]["dependencies"]:
        yield f"{path.parent.name}.project.dependencies", requirement
    for extra, requirements in data["project"].get("optional-dependencies", {}).items():
        for requirement in requirements:
            yield (
                f"{path.parent.name}.project.optional-dependencies.{extra}",
                requirement,
            )
    for requirement in data["build-system"]["requires"]:
        yield f"{path.parent.name}.build-system.requires", requirement


def test_direct_dependencies_use_exact_versions():
    root_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    requirements = list(_root_requirements(root_data))

    for path in PACKAGE_PYPROJECTS:
        data = tomllib.loads(path.read_text())
        requirements.extend(_package_requirements(path, data))

    invalid = []
    for location, raw_requirement in requirements:
        requirement = Requirement(raw_requirement)
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==":
            invalid.append(f"{location}: {raw_requirement}")

    assert not invalid, "Dependencies without one exact == pin:\n" + "\n".join(invalid)
