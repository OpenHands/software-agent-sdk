#!/usr/bin/env python3
"""Update the pinned OpenVSCode Server release in the agent-server Dockerfile."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path


REPOSITORY = "gitpod-io/openvscode-server"
TAG_RE = re.compile(r"^openvscode-server-v(\d+\.\d+\.\d+)$")
PIN_RE = re.compile(r'(?m)^ARG RELEASE_TAG="(openvscode-server-v\d+\.\d+\.\d+)"$')
ARCHITECTURES = ("x64", "arm64")


def fetch_latest_tag() -> str:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}/releases/latest", headers=headers
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["tag_name"]


def validate_tag(tag: str) -> None:
    if not TAG_RE.fullmatch(tag):
        raise ValueError(f"unexpected OpenVSCode release tag: {tag!r}")


def verify_artifacts(tag: str) -> None:
    for arch in ARCHITECTURES:
        filename = f"{tag}-linux-{arch}.tar.gz"
        request = urllib.request.Request(
            f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}",
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError(f"{filename} returned HTTP {response.status}")


def update_dockerfile(path: Path, tag: str) -> bool:
    text = path.read_text(encoding="utf-8")
    matches = PIN_RE.findall(text)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one OpenVSCode release pin in {path}")
    updated = PIN_RE.sub(f'ARG RELEASE_TAG="{tag}"', text)
    if updated == text:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument("--tag")
    parser.add_argument("--skip-artifact-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tag = args.tag or fetch_latest_tag()
    validate_tag(tag)
    if not args.skip_artifact_check:
        verify_artifacts(tag)
    changed = update_dockerfile(args.dockerfile, tag)
    print(f"OpenVSCode Server: {tag} ({'updated' if changed else 'unchanged'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
