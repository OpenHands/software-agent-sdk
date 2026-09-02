#!/usr/bin/env python3
"""Export the deterministic public Agent Server OpenAPI contract."""

from __future__ import annotations

import argparse
from pathlib import Path

from openhands.agent_server.openapi import build_public_openapi, serialize_openapi


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write the public OpenAPI JSON document.",
    )
    return parser.parse_args()


def export_openapi(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialize_openapi(build_public_openapi()))


def main() -> int:
    args = parse_args()
    export_openapi(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
