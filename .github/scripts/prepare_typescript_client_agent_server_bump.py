#!/usr/bin/env python3
"""Prepare a TypeScript client checkout for an exact Agent Server release.

The release workflow owns artifact acquisition and client generation. This
script validates the acquired OpenAPI document and updates every checked-in
mirror of ``package.json.config.agentServerImage`` in one deterministic step.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
IMAGE_RE = re.compile(
    r"^ghcr\.io/openhands/agent-server:(?P<version>\d+\.\d+\.\d+)-python$"
)
VERSION_MIRRORS = (
    Path("package.json"),
    Path(".github/workflows/integration-tests.yml"),
    Path("AGENTS.md"),
    Path("README.md"),
)


@dataclass(frozen=True)
class BumpResult:
    previous_version: str
    target_version: str
    changed_files: tuple[Path, ...]


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected {path} to contain a JSON object")
    return value


def validate_openapi_artifact(path: Path, expected_version: str) -> None:
    document = _load_json_object(path)
    actual_version = document.get("info", {}).get("version")
    if actual_version != expected_version:
        raise ValueError(
            f"OpenAPI artifact version {actual_version!r} does not match "
            f"Agent Server release {expected_version!r}"
        )
    if not isinstance(document.get("openapi"), str):
        raise ValueError("OpenAPI artifact has no string 'openapi' version")
    if not isinstance(document.get("paths"), dict):
        raise ValueError("OpenAPI artifact has no paths object")
    if not isinstance(document.get("components", {}).get("schemas"), dict):
        raise ValueError("OpenAPI artifact has no components.schemas object")


def _current_version(package_json: dict[str, Any]) -> str:
    image = package_json.get("config", {}).get("agentServerImage")
    if not isinstance(image, str) or not (match := IMAGE_RE.fullmatch(image)):
        raise ValueError(
            "package.json config.agentServerImage must be an exact "
            "ghcr.io/openhands/agent-server:X.Y.Z-python release image"
        )
    return match.group("version")


def _replace_version_mirror(text: str, previous: str, target: str) -> str:
    replacements = (
        (
            f"ghcr.io/openhands/agent-server:{previous}-python",
            f"ghcr.io/openhands/agent-server:{target}-python",
        ),
        (
            f"software-agent-sdk v{previous}",
            f"software-agent-sdk v{target}",
        ),
        (f"`v{previous}`", f"`v{target}`"),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def prepare_bump(
    client_root: Path,
    *,
    target_version: str,
    openapi_artifact: Path,
) -> BumpResult:
    if not VERSION_RE.fullmatch(target_version):
        raise ValueError(
            f"Invalid Agent Server version {target_version!r}; expected X.Y.Z"
        )
    validate_openapi_artifact(openapi_artifact, target_version)

    package_path = client_root / "package.json"
    previous_version = _current_version(_load_json_object(package_path))
    changed_files: list[Path] = []

    for relative_path in VERSION_MIRRORS:
        path = client_root / relative_path
        try:
            previous_text = path.read_text()
        except OSError as exc:
            raise ValueError(
                f"Unable to read required client file {path}: {exc}"
            ) from exc
        next_text = _replace_version_mirror(
            previous_text,
            previous_version,
            target_version,
        )
        if next_text != previous_text:
            path.write_text(next_text)
            changed_files.append(relative_path)

    updated_version = _current_version(_load_json_object(package_path))
    if updated_version != target_version:
        raise ValueError(
            "package.json was not updated to the requested Agent Server release: "
            f"found {updated_version!r}, expected {target_version!r}"
        )

    return BumpResult(
        previous_version=previous_version,
        target_version=target_version,
        changed_files=tuple(changed_files),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--openapi", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = prepare_bump(
            args.client_root,
            target_version=args.version,
            openapi_artifact=args.openapi,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    changed = ", ".join(path.as_posix() for path in result.changed_files) or "none"
    print(
        f"Prepared TypeScript client Agent Server bump "
        f"{result.previous_version} -> {result.target_version}; "
        f"changed files: {changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
