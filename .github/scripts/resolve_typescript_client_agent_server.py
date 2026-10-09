#!/usr/bin/env python3
"""Report whether the TypeScript client's pinned Agent Server is released yet.

A release PR pins the client to the version it is about to publish, so that
release's openapi.json and image only exist after the PR merges. CI uses these
outputs to read the contract from the branch's own source in that window.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path


IMAGE_RE = re.compile(
    r"^ghcr\.io/openhands/agent-server:(?P<version>\d+\.\d+\.\d+)-python$"
)
OPENAPI_URL = (
    "https://github.com/OpenHands/software-agent-sdk/releases/download/"
    "v{version}/openapi.json"
)


def pinned_version(client_root: Path) -> str:
    package = json.loads((client_root / "package.json").read_text())
    image = package.get("config", {}).get("agentServerImage")
    match = IMAGE_RE.match(image) if isinstance(image, str) else None
    if not match:
        raise SystemExit(
            f"config.agentServerImage must use an exact release tag, got {image!r}"
        )
    return match["version"]


def source_version(pyproject: Path) -> str:
    return tomllib.loads(pyproject.read_text())["project"]["version"]


def contract_published(version: str) -> bool:
    try:
        with urllib.request.urlopen(OPENAPI_URL.format(version=version), timeout=30):
            return True
    except urllib.error.HTTPError as exc:
        # Anything other than 404 is a transient failure, not "unreleased".
        if exc.code == 404:
            return False
        raise


def resolve(client_root: Path, pyproject: Path) -> dict[str, str]:
    pinned = pinned_version(client_root)
    pinned_is_source = pinned == source_version(pyproject)
    use_source = pinned_is_source and not contract_published(pinned)
    return {
        "pinned_version": pinned,
        "pinned_is_source": str(pinned_is_source).lower(),
        "use_source_contract": str(use_source).lower(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-root", type=Path, default=Path("clients/typescript"))
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("openhands-agent-server/pyproject.toml"),
    )
    args = parser.parse_args()

    outputs = resolve(args.client_root, args.pyproject)
    lines = "".join(f"{key}={value}\n" for key, value in outputs.items())
    print(lines, end="")
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a") as fh:
            fh.write(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
