from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from openhands.sdk.utils.github import sanitize_openhands_mentions


@dataclass(slots=True)
class ExampleResult:
    name: str
    status: str
    duration_seconds: float | None
    cost: str | None
    failure_reason: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render markdown summary for example runs."
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory containing per-example JSON results.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="Unknown model",
        help="LLM model name used for the run.",
    )
    parser.add_argument(
        "--workflow-url",
        type=str,
        default="",
        help="URL to the workflow run details page.",
    )
    parser.add_argument(
        "--timestamp",
        type=str,
        default="",
        help="UTC timestamp string to include in the report header.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the markdown report to.",
    )
    return parser.parse_args()


def iter_result_files(results_dir: Path) -> Iterable[Path]:
    yield from sorted(results_dir.glob("*.json"))


def load_results(results_dir: Path) -> list[ExampleResult]:
    results: list[ExampleResult] = []
    for path in iter_result_files(results_dir):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        results.append(
            ExampleResult(
                name=str(payload.get("example", path.stem)),
                status=str(payload.get("status", "unknown")),
                duration_seconds=_coerce_float(payload.get("duration_seconds")),
                cost=_coerce_cost(payload.get("cost")),
                failure_reason=_sanitize_reason(payload.get("failure_reason")),
            )
        )
    return sorted(results, key=lambda item: item.name)


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _coerce_cost(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return str(value)


def _sanitize_reason(value: object) -> str | None:
    if value is None:
        return None
    reason = str(value).strip()
    return reason or None


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, sec = divmod(int(seconds + 0.5), 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def format_cost(value: str | None) -> str:
    if not value:
        return "--"
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return "--"
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${quantized}"


def format_total_cost(values: Iterable[str | None]) -> str | None:
    total = Decimal("0")
    seen = False
    for value in values:
        if not value:
            continue
        try:
            amount = Decimal(value)
        except InvalidOperation:
            continue
        total += amount
        seen = True
    if not seen:
        return None
    quantized = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${quantized}"


def markdown_header(model: str, timestamp: str) -> list[str]:
    ts = timestamp or datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return [f"## 🔄 Running Examples with `{model}`", "", f"_Generated: {ts}_", ""]


def markdown_table(results: list[ExampleResult]) -> list[str]:
    lines = [
        "| Example | Status | Duration | Cost |",
        "|---------|--------|----------|------|",
    ]
    for result in results:
        example = result.name
        if example.startswith("examples/"):
            example = example[len("examples/") :]
        status = "✅ PASS" if result.status == "passed" else "❌ FAIL"
        if result.status != "passed" and result.failure_reason:
            status = f"{status}<br>{_escape_cell(result.failure_reason)}"
        duration_display = format_duration(result.duration_seconds)
        cost_display = format_cost(result.cost)
        cells = [
            _escape_cell(example),
            status,
            duration_display,
            cost_display,
        ]
        row = "| " + " | ".join(cells) + " |"
        lines.append(row)
    if len(results) == 0:
        lines.append("| _No results_ | -- | -- | -- |")
    return lines


def markdown_summary(results: list[ExampleResult], workflow_url: str) -> list[str]:
    total = len(results)
    passed = sum(1 for item in results if item.status == "passed")
    failed = total - passed
    cost_summary = format_total_cost(item.cost for item in results)

    lines = ["", "---", ""]
    if failed == 0 and total > 0:
        lines.append("### ✅ All tests passed!")
    elif failed == 0:
        lines.append("### ℹ️ No examples were executed")
    else:
        lines.append("### ❌ Some tests failed")

    summary = f"**Total:** {total} | **Passed:** {passed} | **Failed:** {failed}"
    if cost_summary:
        summary += f" | **Total Cost:** {cost_summary}"
    lines.append(summary)

    if failed:
        lines.append("")
        lines.append("**Failed examples:**")
        for item in results:
            if item.status != "passed":
                reason = item.failure_reason or "See logs"
                lines.append(f"- {item.name}: {reason}")

    if workflow_url:
        lines.append("")
        lines.append(f"[View full workflow run]({workflow_url})")

    return lines


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", "<br>")


def build_report(args: argparse.Namespace, results: list[ExampleResult]) -> str:
    lines = markdown_header(args.model, args.timestamp)
    lines.extend(markdown_table(results))
    lines.extend(markdown_summary(results, args.workflow_url))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    results = load_results(args.results_dir)
    report = build_report(args, results)
    sanitized = sanitize_openhands_mentions(report)

    if args.output is not None:
        args.output.write_text(sanitized)

    print(sanitized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
