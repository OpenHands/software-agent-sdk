#!/usr/bin/env python3
"""Insert the generated REST API summary into a PR body's Summary section."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


START_MARKER = "<!-- agent-server-rest-api-contract-summary:start -->"
END_MARKER = "<!-- agent-server-rest-api-contract-summary:end -->"
AGENT_SECTION_RE = re.compile(r"(?m)^AGENT:\s*$")
SUMMARY_HEADING_RE = re.compile(r"(?m)^##\s+Summary\s*$")
NEXT_HEADING_RE = re.compile(r"(?m)^##\s+")
GENERATED_BLOCK_RE = re.compile(
    rf"(?:\r?\n)?{re.escape(START_MARKER)}\r?\n.*?\r?\n"
    rf"{re.escape(END_MARKER)}(?:\r?\n)?",
    re.DOTALL,
)


def _summary_bounds(body: str) -> tuple[int, int] | None:
    agent_match = AGENT_SECTION_RE.search(body)
    if agent_match is None:
        return None

    summary_match = SUMMARY_HEADING_RE.search(body, agent_match.end())
    if summary_match is None:
        return None

    next_match = NEXT_HEADING_RE.search(body, summary_match.end())
    end = next_match.start() if next_match else len(body)
    return summary_match.end(), end


def _generated_block(summary: str) -> str:
    summary = summary.strip()
    if not summary:
        return ""
    return f"{START_MARKER}\n{summary}\n{END_MARKER}"


def update_body(body: str, generated_summary: str) -> str:
    if not generated_summary.strip() and START_MARKER not in body:
        return body

    bounds = _summary_bounds(body)
    if bounds is None:
        return body

    start, end = bounds
    summary_section = body[start:end]
    cleaned_section = GENERATED_BLOCK_RE.sub("\n", summary_section).rstrip()
    block = _generated_block(generated_summary)
    if block:
        if cleaned_section.strip():
            replacement = f"{cleaned_section}\n\n{block}\n\n"
        else:
            replacement = f"\n\n{block}\n\n"
    else:
        replacement = f"{cleaned_section}\n" if cleaned_section else "\n"

    return f"{body[:start]}{replacement}{body[end:]}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update a PR body with a generated REST API summary block."
    )
    parser.add_argument("--body-file", type=Path, required=True)
    parser.add_argument("--summary-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with args.body_file.open(newline="") as body_file:
        body = body_file.read()
    with args.summary_file.open(newline="") as summary_file:
        generated_summary = summary_file.read()
    with args.output.open("w", newline="") as output_file:
        output_file.write(update_body(body, generated_summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
