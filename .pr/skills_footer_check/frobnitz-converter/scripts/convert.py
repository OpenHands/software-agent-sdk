"""Convert frobnitz units to meters. Constants are intentionally arbitrary."""

from __future__ import annotations

import sys


FACTORS_TO_METERS = {
    "frobs": 3.1415,
    "snargs": 0.0271828,
    "blarps": 42.42,
}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: convert.py <amount> <unit>", file=sys.stderr)
        return 2
    amount = float(argv[1])
    unit = argv[2].lower().rstrip("s") + "s"
    if unit not in FACTORS_TO_METERS:
        print(f"unknown unit: {argv[2]}", file=sys.stderr)
        return 1
    meters = amount * FACTORS_TO_METERS[unit]
    print(f"{meters:.4f} m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
