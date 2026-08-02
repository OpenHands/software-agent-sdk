"""Generate an execution report inside the local or Pyromind runtime.

Scans runtime-mounted files in the given log directory and produces a
``report.json`` summary with statistics, error samples, and timing. Agents
inspect remote copies of these artifacts through ``preview_dataset``; this
script does not provide a remote-storage access path.

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


def _read_json_object(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _read_dataflow_checkpoint(
    log_path: Path,
    runtime_metadata: dict | None,
    *,
    output_records: int,
) -> dict | None:
    candidates = sorted(log_path.glob("*_last_success_step.txt"))
    if not candidates:
        return None
    checkpoint_path = candidates[0]
    try:
        operator_step, next_batch = (
            int(value)
            for value in checkpoint_path.read_text(encoding="utf-8")
            .strip()
            .split(
                ",",
                1,
            )
        )
    except (OSError, TypeError, ValueError):
        return None
    batch_size = int((runtime_metadata or {}).get("batch_size") or 0)
    record_count = int((runtime_metadata or {}).get("record_count") or 0)
    next_source_index = (
        min(next_batch * batch_size, record_count)
        if operator_step == 0 and batch_size > 0
        else output_records
    )
    return {
        "kind": "dataflow_batch",
        "path": checkpoint_path.name,
        "operator_step": operator_step,
        "next_batch": next_batch,
        "batch_size": batch_size or None,
        "next_source_index": next_source_index,
        "committed_records": output_records,
    }


def generate_report(
    log_dir: str,
    *,
    pipeline_exit_code: int = 0,
    execution_revision: int = 1,
    resumed: bool = False,
    reuse_assessment: dict | None = None,
    output_file: str | None = None,
    runtime_fingerprint: str | None = None,
    runtime_dir_name: str = "",
    image_utils_api_version: str | None = None,
) -> dict:
    """Scan llm_calls.jsonl and produce a report dict."""
    log_path = Path(log_dir)
    calls_file = log_path / "llm_calls.jsonl"
    processed_file = Path(output_file) if output_file else log_path / "processed.jsonl"

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

    validation = _read_json_object(log_path / "validation.json")
    scenario_metrics = _read_json_object(log_path / "scenario_metrics.json")
    runtime_metadata = _read_json_object(log_path / "runtime_metadata.json")
    checkpoint = _read_json_object(log_path / "checkpoint.json")
    if checkpoint is None:
        checkpoint = _read_dataflow_checkpoint(
            log_path,
            runtime_metadata,
            output_records=output_records,
        )
    runtime_failure = _read_json_object(log_path / "failure.json")

    # Determine overall status
    if pipeline_exit_code != 0:
        overall = "failed"
    elif validation is not None and validation.get("status") != "passed":
        overall = "failed"
    elif validation is not None and validation.get("status") == "passed":
        overall = "succeeded"
    elif total_calls == 0:
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
        "pipeline_exit_code": pipeline_exit_code,
        "execution_revision": execution_revision,
        "resumed": resumed,
        "resumable": overall == "failed" and checkpoint is not None,
        "recommended_action": ("agent_assess" if overall == "failed" else "none"),
        "reuse_assessment": reuse_assessment,
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
        "runtime_fingerprint": (
            runtime_fingerprint or (runtime_metadata or {}).get("runtime_fingerprint")
        ),
        "runtime_dir_name": runtime_dir_name,
        "image_utils_api_version": (
            image_utils_api_version
            or (runtime_metadata or {}).get("image_utils_api_version")
        ),
        "runtime_metadata": runtime_metadata,
        "checkpoint": checkpoint,
        "failure": runtime_failure,
        "validation": validation,
        "scenario_metrics": scenario_metrics,
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
    parser.add_argument("--pipeline-exit-code", type=int, default=0)
    parser.add_argument("--execution-revision", type=int, default=1)
    parser.add_argument("--resumed", choices=("true", "false"), default="false")
    parser.add_argument("--reuse-assessment-json")
    parser.add_argument("--output-file")
    parser.add_argument("--runtime-fingerprint")
    parser.add_argument("--runtime-dir-name", default="")
    parser.add_argument("--image-utils-api-version")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.is_dir():
        print(f"Error: log directory not found: {log_dir}", file=sys.stderr)
        sys.exit(1)

    reuse_assessment = None
    if args.reuse_assessment_json:
        try:
            reuse_assessment = json.loads(args.reuse_assessment_json)
        except json.JSONDecodeError as exc:
            print(f"Invalid --reuse-assessment-json: {exc}", file=sys.stderr)
            sys.exit(2)
    report = generate_report(
        args.log_dir,
        pipeline_exit_code=args.pipeline_exit_code,
        execution_revision=args.execution_revision,
        resumed=args.resumed == "true",
        reuse_assessment=reuse_assessment,
        output_file=args.output_file,
        runtime_fingerprint=args.runtime_fingerprint,
        runtime_dir_name=args.runtime_dir_name,
        image_utils_api_version=args.image_utils_api_version,
    )
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
