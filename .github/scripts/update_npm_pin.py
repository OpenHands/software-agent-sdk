#!/usr/bin/env python3
"""Select an observed stable npm release after a seven-day window.

The default stays on the current major: crossing a major (npm only
guarantees bundled-dependency fixes and engines requirements per major) is
a deliberate policy choice, not something to smuggle in as a side effect
of a version-bump PR. Pass --allow-major to opt in, as the scheduled
workflow does today for the npm 12 line, which is required for the fixed
bundled undici/brace-expansion. The seven-day window applies either way.
"""

import argparse
import json
import os
import re
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


PIN = re.compile(r"(?m)^ARG NPM_VERSION=(\d+\.\d+\.\d+)$")
VERSION = re.compile(r"\d+\.\d+\.\d+")


def select_version(
    metadata: dict, current: str, now: datetime, *, allow_major: bool = False
) -> str:
    cutoff = now - timedelta(days=7)
    current_parts = tuple(map(int, current.split(".")))
    candidates = [current_parts]
    for version, release in metadata["versions"].items():
        if not VERSION.fullmatch(version) or release.get("deprecated"):
            continue
        parts = tuple(map(int, version.split(".")))
        if parts <= current_parts:
            continue
        if not allow_major and parts[0] != current_parts[0]:
            continue
        published = datetime.fromisoformat(
            metadata["time"][version].replace("Z", "+00:00")
        )
        if published <= cutoff:
            candidates.append(parts)
    return ".".join(map(str, max(candidates)))


def _allow_major_from_env() -> bool:
    return os.environ.get("ALLOW_MAJOR_UPGRADE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def update_pin(
    path: Path, metadata: dict, now: datetime, *, allow_major: bool | None = None
) -> str:
    if allow_major is None:
        allow_major = _allow_major_from_env()
    text = path.read_text()
    matches = PIN.findall(text)
    if len(matches) != 1:
        raise ValueError("expected exactly one NPM_VERSION pin")
    selected = select_version(metadata, matches[0], now, allow_major=allow_major)
    if selected != matches[0]:
        path.write_text(PIN.sub(f"ARG NPM_VERSION={selected}", text))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, required=True)
    parser.add_argument(
        "--allow-major",
        action="store_true",
        default=None,
        help="Allow crossing to a newer major (default: ALLOW_MAJOR_UPGRADE env)",
    )
    args = parser.parse_args()
    allow_major = args.allow_major
    if allow_major is None:
        allow_major = _allow_major_from_env()
    with urllib.request.urlopen(
        "https://registry.npmjs.org/npm", timeout=30
    ) as response:
        metadata = json.load(response)
    print(
        update_pin(
            args.dockerfile, metadata, datetime.now(UTC), allow_major=allow_major
        )
    )


if __name__ == "__main__":
    main()
