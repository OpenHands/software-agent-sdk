#!/usr/bin/env python3
"""Select the newest stable code-server release after a 7-day observation.

Refreshes the Dockerfile's CODE_SERVER_VERSION pin and both per-architecture
release digests atomically, so the editor cannot silently age while the rest
of the image moves forward. Only stable, non-draft GitHub releases that have
completed the observation window are eligible, and both Linux release assets
must carry a sha256 digest. On any doubt the script raises and the scheduled
workflow fails closed instead of writing a partial pin. This is a persistent
refresh mechanism like the npm pin workflow, not a one-off override: it
keeps running after this script's introduction.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


RELEASES_URL = "https://api.github.com/repos/coder/code-server/releases?per_page=100"
OBSERVATION_PERIOD = timedelta(days=7)
VERSION = re.compile(r"\d+\.\d+\.\d+")
PIN_VERSION = re.compile(r'(?m)^ARG CODE_SERVER_VERSION="(\d+\.\d+\.\d+)"$')
PIN_AMD64 = re.compile(r"(?m)^ARG CODE_SERVER_AMD64_SHA256=([0-9a-f]{64})$")
PIN_ARM64 = re.compile(r"(?m)^ARG CODE_SERVER_ARM64_SHA256=([0-9a-f]{64})$")
ASSETS = {
    "amd64": "code-server-{version}-linux-amd64.tar.gz",
    "arm64": "code-server-{version}-linux-arm64.tar.gz",
}


def _version_tuple(version: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _asset_digest(release: dict, version: str, arch: str) -> str:
    expected = ASSETS[arch].format(version=version)
    for asset in release.get("assets", []):
        if asset.get("name") != expected:
            continue
        digest = asset.get("digest") or ""
        match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest)
        if match is None:
            raise ValueError(f"release {version} asset {expected} has no sha256 digest")
        return match.group(1)
    raise ValueError(f"release {version} is missing asset {expected}")


def select_release(releases: list[dict], current: str, now: datetime) -> dict:
    """Return the newest eligible release entry, or the current pin."""
    cutoff = now - OBSERVATION_PERIOD
    current_parts = _version_tuple(current)
    best: tuple[tuple[int, int, int], dict] | None = None
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name") or ""
        version = tag[1:] if tag.startswith("v") else tag
        if not VERSION.fullmatch(version):
            continue
        parts = _version_tuple(version)
        if parts <= current_parts:
            continue
        published = datetime.fromisoformat(
            str(release["published_at"]).replace("Z", "+00:00")
        )
        if published > cutoff:
            continue
        if best is None or parts > best[0]:
            best = (parts, release)
    if best is None:
        return {"tag_name": f"v{current}", "assets": []}
    return best[1]


def update_pin(path: Path, releases: list[dict], now: datetime) -> str:
    text = path.read_text(encoding="utf-8")
    version_matches = PIN_VERSION.findall(text)
    if len(version_matches) != 1:
        raise ValueError("expected exactly one CODE_SERVER_VERSION pin")
    current = version_matches[0]
    selected = select_release(releases, current, now)
    selected_version = str(selected["tag_name"]).removeprefix("v")
    if selected_version == current:
        return current

    # Verify both digests before touching the file: all three pins must
    # refer to the same release, so a partial or mismatched update would
    # otherwise produce a Dockerfile that can never build.
    digests = {arch: _asset_digest(selected, selected_version, arch) for arch in ASSETS}
    updated = PIN_VERSION.sub(f'ARG CODE_SERVER_VERSION="{selected_version}"', text)
    updated, amd64_count = PIN_AMD64.subn(
        f"ARG CODE_SERVER_AMD64_SHA256={digests['amd64']}", updated
    )
    updated, arm64_count = PIN_ARM64.subn(
        f"ARG CODE_SERVER_ARM64_SHA256={digests['arm64']}", updated
    )
    if amd64_count != 1 or arm64_count != 1:
        raise ValueError("expected exactly one code-server digest pin per arch")
    path.write_text(updated, encoding="utf-8")
    return selected_version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, required=True)
    args = parser.parse_args()
    request = urllib.request.Request(
        RELEASES_URL,
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    print(update_pin(args.dockerfile, releases, datetime.now(UTC)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
