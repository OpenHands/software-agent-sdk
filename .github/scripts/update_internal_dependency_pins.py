"""Keep exact workspace dependency pins aligned with package version bumps."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from pathlib import Path


PACKAGE_PYPROJECTS = (
    Path("openhands-sdk/pyproject.toml"),
    Path("openhands-tools/pyproject.toml"),
    Path("openhands-workspace/pyproject.toml"),
    Path("openhands-agent-server/pyproject.toml"),
)
WORKSPACE_PACKAGES = frozenset(path.parent.name for path in PACKAGE_PYPROJECTS)
_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?\Z")


def update_internal_dependency_pins(
    repo_root: Path,
    version: str,
    packages: Iterable[str],
) -> list[Path]:
    """Update exact requirements on the selected workspace packages."""
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"Invalid package version: {version}")

    selected = frozenset(packages)
    unknown = selected - WORKSPACE_PACKAGES
    if unknown:
        raise ValueError(f"Unknown workspace packages: {', '.join(sorted(unknown))}")

    patterns = [
        re.compile(rf'("{re.escape(package)}==)[^"]+(")')
        for package in sorted(selected)
    ]
    changed: list[Path] = []

    for relative_path in PACKAGE_PYPROJECTS:
        path = repo_root / relative_path
        original = path.read_text()
        updated = original
        for pattern in patterns:
            updated = pattern.sub(rf"\g<1>{version}\g<2>", updated)

        if updated != original:
            path.write_text(updated)
            changed.append(relative_path)

    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--packages", nargs="+", required=True)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    try:
        changed = update_internal_dependency_pins(
            repo_root,
            args.version,
            args.packages,
        )
    except ValueError as exc:
        parser.error(str(exc))

    for path in changed:
        print(f"Updated internal dependency pins in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
