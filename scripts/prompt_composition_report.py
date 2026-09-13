#!/usr/bin/env python3
"""Offline per-call prompt token composition report from completion logs.

Usage:
    uv run python scripts/prompt_composition_report.py --root LOGS [--out DIR]
        [--no-chart]

``--root`` follows the completion-log layout documented in
``scripts/completion_logs_viewer.py``: either a single run folder of ``*.json``
logs, or a root directory containing such run folders. The logs are the files
written by ``LLM(log_completions=True)``.

For each log the script rebuilds the prompt token composition (system prompt,
tool schemas, history, latest message) from the logged request payload, joins
it with the provider-reported usage, prints a text visualization, and
optionally writes the per-call rows (``calls.jsonl``) and aggregate
``summary.json`` into ``--out``.
"""

import argparse
import sys
from pathlib import Path

from openhands.sdk.llm.utils.prompt_composition_report import (
    build_report,
    render_text_report,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        required=True,
        type=Path,
        help="Run folder of *.json completion logs, or a root of run folders",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory to write calls.jsonl and summary.json into",
    )
    parser.add_argument(
        "--no-chart",
        action="store_true",
        help="Skip the text visualization on stdout",
    )
    args = parser.parse_args()

    report = build_report(args.root)
    if not report["rows"]:
        print(f"No countable LLM call logs found under {args.root}", file=sys.stderr)
        return 1

    if not args.no_chart:
        print(render_text_report(report))
    if args.out:
        calls_path, summary_path = write_report(report, args.out)
        print(f"\nWrote {calls_path} and {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
