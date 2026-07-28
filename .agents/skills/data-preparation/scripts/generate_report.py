"""Generate an execution report from DataFlow pipeline LLM call logs.

Scans ``llm_calls.jsonl`` in the given log directory and produces a
``report.json`` summary with statistics, error samples, and timing.

Usage (called automatically by the platform command after pipeline completes):

    python generate_report.py --log-dir /path/to/run_dir

The report is written to ``<log_dir>/report.json``.
"""

from __future__ import annotations  # noqa: I001

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from datetime import UTC as _UTC  # Python 3.11+
except ImportError:
    from datetime import timezone

    _UTC = timezone.utc  # noqa: UP017 — Python 3.10


MAX_ERROR_SAMPLES = 20
INPUT_PREVIEW_CHARS = 200


def _count_jsonl_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def _extract_input_preview(record: dict) -> str:
    """Extract a short preview of the input from a call record."""
    messages = record.get("request_messages")
    if isinstance(messages, list) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            content = last.get("content", "")
            return str(content)[:INPUT_PREVIEW_CHARS]
    payload = record.get("request_payload", "")
    return str(payload)[:INPUT_PREVIEW_CHARS]


def generate_report(log_dir: str) -> dict:
    """Scan llm_calls.jsonl and produce a report dict."""
    log_path = Path(log_dir)
    calls_file = log_path / "llm_calls.jsonl"
    processed_file = log_path / "processed.jsonl"

    total_calls = 0
    success_calls = 0
    failed_calls = 0
    latencies: list[float] = []
    errors: list[dict] = []
    first_ts: str | None = None
    last_ts: str | None = None

    if calls_file.is_file():
        with open(calls_file, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total_calls += 1
                status = record.get("status", "unknown")
                latency = record.get("latency_ms", 0)
                latencies.append(latency)
                ts = record.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                if status == "success":
                    success_calls += 1
                else:
                    failed_calls += 1
                    if len(errors) < MAX_ERROR_SAMPLES:
                        errors.append(
                            {
                                "seq": record.get("seq"),
                                "status": status,
                                "error": record.get("error"),
                                "latency_ms": latency,
                                "input_preview": _extract_input_preview(record),
                            }
                        )

    # Compute latency percentiles
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0
    sorted_lat = sorted(latencies)
    p95_latency = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0
    max_latency = sorted_lat[-1] if sorted_lat else 0

    # Compute duration from first/last timestamps
    duration_seconds = None
    if first_ts and last_ts:
        try:
            t0 = datetime.fromisoformat(first_ts)
            t1 = datetime.fromisoformat(last_ts)
            duration_seconds = round((t1 - t0).total_seconds(), 1)
        except (ValueError, TypeError):
            pass

    # Count output records
    output_records = _count_jsonl_lines(processed_file)

    # Determine overall status
    if total_calls == 0:
        overall = "no_llm_calls"
    elif failed_calls == 0:
        overall = "succeeded"
    elif success_calls == 0:
        overall = "failed"
    else:
        overall = "partial_failure"

    # Try to get dataflow version
    dataflow_version = None
    try:
        import dataflow

        dataflow_version = getattr(dataflow, "__version__", None)
    except ImportError:
        pass

    report = {
        "status": overall,
        "generated_at": datetime.now(_UTC).isoformat(),
        "total_records_output": output_records,
        "llm_calls": {
            "total": total_calls,
            "success": success_calls,
            "failed": failed_calls,
            "success_rate": (
                round(success_calls / total_calls * 100, 1) if total_calls > 0 else 0
            ),
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "max_latency_ms": max_latency,
        },
        "duration_seconds": duration_seconds,
        "error_samples": errors,
        "dataflow_version": dataflow_version,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate report.json from LLM call logs."
    )
    parser.add_argument(
        "--log-dir",
        required=True,
        help="Directory containing llm_calls.jsonl and processed.jsonl",
    )
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.is_dir():
        print(f"Error: log directory not found: {log_dir}", file=sys.stderr)
        sys.exit(1)

    report = generate_report(args.log_dir)
    report_path = log_dir / "report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report written to {report_path}")
    print(
        f"  status={report['status']} "
        f"calls={report['llm_calls']['total']} "
        f"success={report['llm_calls']['success']} "
        f"failed={report['llm_calls']['failed']} "
        f"output_records={report['total_records_output']}"
    )


if __name__ == "__main__":
    main()
