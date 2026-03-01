#!/usr/bin/env python3
"""
Generate flame graphs from Austin profiler output.

This script converts Austin profiler output to:
1. Speedscope JSON format (for interactive web-based analysis)
2. Collapsed stack format (for flamegraph.pl SVG generation)
3. Summary statistics

Usage:
    python scripts/generate_flamegraph.py --input profile.austin --output-dir ./output

The generated files can be:
- SVG: Opened directly in a browser
- Speedscope JSON: Uploaded to https://speedscope.app for interactive analysis
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProfileStats:
    """Statistics from profiling data."""

    total_samples: int = 0
    unique_stacks: int = 0
    top_functions: dict[str, int] = field(default_factory=dict)
    top_stacks: list[tuple[str, int]] = field(default_factory=list)


def parse_austin_file(input_path: Path) -> tuple[dict[str, int], ProfileStats]:
    """Parse Austin output file and return collapsed stacks with statistics.

    Austin format: P<pid>;T<tid>;frame1;frame2;... count
    Or without PID/TID: frame1;frame2;... count

    Args:
        input_path: Path to Austin .austin file

    Returns:
        Tuple of (collapsed_stacks dict, ProfileStats)
    """
    collapsed: dict[str, int] = defaultdict(int)
    function_counts: dict[str, int] = defaultdict(int)
    total_samples = 0

    with open(input_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Split into stack and count
            parts = line.rsplit(" ", 1)
            if len(parts) != 2:
                continue

            stack_str, count_str = parts
            try:
                count = int(count_str)
            except ValueError:
                continue

            total_samples += count

            # Parse stack - remove P<pid>;T<tid> prefix if present
            stack_parts = stack_str.split(";")
            if (
                len(stack_parts) >= 2
                and stack_parts[0].startswith("P")
                and stack_parts[1].startswith("T")
            ):
                stack_parts = stack_parts[2:]

            if not stack_parts:
                continue

            # Build clean stack string
            clean_stack = ";".join(stack_parts)
            collapsed[clean_stack] += count

            # Track function-level stats (leaf function)
            if stack_parts:
                leaf_function = stack_parts[-1]
                function_counts[leaf_function] += count

    # Build stats
    sorted_functions = sorted(function_counts.items(), key=lambda x: -x[1])
    sorted_stacks = sorted(collapsed.items(), key=lambda x: -x[1])

    stats = ProfileStats(
        total_samples=total_samples,
        unique_stacks=len(collapsed),
        top_functions=dict(sorted_functions[:20]),
        top_stacks=sorted_stacks[:10],
    )

    return dict(collapsed), stats


def write_collapsed_format(collapsed: dict[str, int], output_path: Path) -> None:
    """Write stacks in collapsed format for flamegraph.pl.

    Format: stack;stack;stack count
    """
    with open(output_path, "w", encoding="utf-8") as f:
        for stack, count in sorted(collapsed.items()):
            f.write(f"{stack} {count}\n")


def write_speedscope_format(
    collapsed: dict[str, int], output_path: Path, name: str = "profile"
) -> None:
    """Write stacks in Speedscope JSON format.

    Speedscope format reference: https://github.com/jlfwong/speedscope/wiki/Importing-from-custom-sources
    """
    # Build frame index
    frames: list[dict[str, str]] = []
    frame_to_idx: dict[str, int] = {}

    def get_frame_idx(frame_name: str) -> int:
        if frame_name not in frame_to_idx:
            frame_to_idx[frame_name] = len(frames)
            frames.append({"name": frame_name})
        return frame_to_idx[frame_name]

    # Build samples (sampled format)
    samples: list[list[int]] = []
    weights: list[int] = []

    for stack_str, count in collapsed.items():
        if not stack_str:
            continue
        stack_frames = stack_str.split(";")
        # Convert to frame indices (reversed for speedscope - root first)
        sample = [get_frame_idx(f) for f in reversed(stack_frames)]
        samples.append(sample)
        weights.append(count)

    # Build speedscope document
    speedscope_doc = {
        "$schema": "https://www.speedscope.app/file-format-schema.json",
        "version": "1.0.0",
        "name": name,
        "shared": {"frames": frames},
        "profiles": [
            {
                "type": "sampled",
                "name": name,
                "unit": "samples",
                "startValue": 0,
                "endValue": sum(weights),
                "samples": samples,
                "weights": weights,
            }
        ],
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(speedscope_doc, f, indent=2)


def write_summary(stats: ProfileStats, output_path: Path, name: str = "profile") -> str:
    """Generate and write markdown summary of profiling results."""
    lines = [
        f"# Profile Summary: {name}",
        "",
        "## Overview",
        "",
        f"- **Total Samples:** {stats.total_samples:,}",
        f"- **Unique Call Stacks:** {stats.unique_stacks:,}",
        "",
        "## Top Functions by Sample Count",
        "",
        "| Function | Samples | % |",
        "|----------|---------|---|",
    ]

    for func, count in list(stats.top_functions.items())[:15]:
        pct = (count / stats.total_samples * 100) if stats.total_samples else 0
        # Truncate long function names
        display_func = func if len(func) <= 60 else func[:57] + "..."
        lines.append(f"| `{display_func}` | {count:,} | {pct:.1f}% |")

    lines.extend(
        [
            "",
            "## Top Call Stacks",
            "",
        ]
    )

    for i, (stack, count) in enumerate(stats.top_stacks[:5], 1):
        pct = (count / stats.total_samples * 100) if stats.total_samples else 0
        lines.append(f"### Stack #{i} ({count:,} samples, {pct:.1f}%)")
        lines.append("")
        lines.append("```")
        # Show stack bottom-to-top (caller first)
        for frame in stack.split(";"):
            lines.append(f"  {frame}")
        lines.append("```")
        lines.append("")

    summary_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(summary_text)

    return summary_text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate flame graphs from Austin profiler output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="Path to Austin .austin output file",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("."),
        help="Output directory for generated files (default: current directory)",
    )
    parser.add_argument(
        "--name",
        "-n",
        type=str,
        default=None,
        help="Profile name (default: derived from input filename)",
    )
    parser.add_argument(
        "--collapsed-only",
        action="store_true",
        help="Only generate collapsed format (for use with external flamegraph.pl)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress summary output to stdout",
    )

    args = parser.parse_args()

    input_path = args.input
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        return 1

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    name = args.name or input_path.stem

    # Parse Austin file
    if not args.quiet:
        print(f"Parsing {input_path}...")

    collapsed, stats = parse_austin_file(input_path)

    if not collapsed:
        print("Warning: No samples found in input file", file=sys.stderr)
        return 0

    # Write collapsed format
    collapsed_path = output_dir / f"{name}.collapsed"
    write_collapsed_format(collapsed, collapsed_path)
    if not args.quiet:
        print(f"  Created: {collapsed_path}")

    if not args.collapsed_only:
        # Write speedscope format
        speedscope_path = output_dir / f"{name}.speedscope.json"
        write_speedscope_format(collapsed, speedscope_path, name)
        if not args.quiet:
            print(f"  Created: {speedscope_path}")

        # Write summary
        summary_path = output_dir / f"{name}.summary.md"
        summary_text = write_summary(stats, summary_path, name)
        if not args.quiet:
            print(f"  Created: {summary_path}")
            print()
            print(summary_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
