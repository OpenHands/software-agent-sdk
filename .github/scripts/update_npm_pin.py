#!/usr/bin/env python3
"""Select stable same-major npm releases after a seven-day observation period."""

import argparse
import json
import re
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path


PIN = re.compile(r"(?m)^ARG NPM_VERSION=(\d+\.\d+\.\d+)$")
VERSION = re.compile(r"\d+\.\d+\.\d+")


def select_version(metadata: dict, current: str, now: datetime) -> str:
    cutoff = now - timedelta(days=7)
    current_parts = tuple(map(int, current.split(".")))
    candidates = [current_parts]
    for version, release in metadata["versions"].items():
        if not VERSION.fullmatch(version) or release.get("deprecated"):
            continue
        parts = tuple(map(int, version.split(".")))
        if parts[0] != current_parts[0] or parts <= current_parts:
            continue
        published = datetime.fromisoformat(
            metadata["time"][version].replace("Z", "+00:00")
        )
        if published <= cutoff:
            candidates.append(parts)
    return ".".join(map(str, max(candidates)))


def update_pin(path: Path, metadata: dict, now: datetime) -> str:
    text = path.read_text()
    matches = PIN.findall(text)
    if len(matches) != 1:
        raise ValueError("expected exactly one NPM_VERSION pin")
    selected = select_version(metadata, matches[0], now)
    if selected != matches[0]:
        path.write_text(PIN.sub(f"ARG NPM_VERSION={selected}", text))
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dockerfile", type=Path, required=True)
    args = parser.parse_args()
    with urllib.request.urlopen(
        "https://registry.npmjs.org/npm", timeout=30
    ) as response:
        metadata = json.load(response)
    print(update_pin(args.dockerfile, metadata, datetime.now(UTC)))


if __name__ == "__main__":
    main()
