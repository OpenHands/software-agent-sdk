"""Offline prompt-composition report over OpenHands completion logs.

Ingests the JSON logs written by ``LLM(log_completions=True)`` (see
``scripts/completion_logs_viewer.py`` for the directory layout) and rebuilds
the per-call prompt token composition from the logged request payload, joined
with the provider-reported usage. Nothing here runs on the LLM call path.
"""

import json
import statistics
from pathlib import Path
from typing import Any

from litellm import ChatCompletionToolParam

from openhands.sdk.llm.utils.prompt_composition import (
    compute_prompt_composition,
    responses_payload_to_chat_messages,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

BUCKETS = (
    "system_prompt_tokens",
    "tool_schema_tokens",
    "history_tokens",
    "latest_message_tokens",
)

_CHART_WIDTH = 50
_BUCKET_CHARS = {
    "system_prompt_tokens": "S",
    "tool_schema_tokens": "T",
    "history_tokens": "H",
    "latest_message_tokens": "L",
}


def iter_log_files(root: Path) -> list[Path]:
    """List log files under a run folder, or under a root of run folders."""
    root = Path(root)
    if not root.is_dir():
        return []
    direct = sorted(root.glob("*.json"))
    if direct:
        return direct
    return sorted(root.glob("*/*.json"))


def _is_openai_tool_schema(tool: Any) -> bool:
    return (
        isinstance(tool, dict)
        and tool.get("type") == "function"
        and isinstance(tool.get("function"), dict)
    )


def _log_model(data: dict[str, Any], source: str) -> str:
    response = data.get("response")
    if isinstance(response, dict) and isinstance(response.get("model"), str):
        return response["model"]
    # Log filenames are "{model with '/'->'__'}-{timestamp}-{uuid4}.json".
    stem = Path(source).stem
    parts = stem.rsplit("-", 2)
    if len(parts) == 3 and parts[0]:
        return parts[0].replace("__", "/")
    return ""


def call_record_from_log(data: Any, source: str) -> dict[str, Any] | None:
    """Build one report row from a parsed log payload, or None to skip it.

    Returns None for anything that is not a countable LLM call log: unreadable
    shapes, error logs, and payloads whose composition cannot be computed.
    """
    if not isinstance(data, dict) or "error" in data:
        return None

    if data.get("llm_path") == "responses":
        input_items = data.get("input")
        if not isinstance(input_items, list) or not input_items:
            return None
        try:
            messages = responses_payload_to_chat_messages(
                data.get("instructions"), input_items
            )
        except ValueError:
            return None
        schemas_in_prompt = False
    else:
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        # Non-native/mock-tools path: schemas are rendered into the prompt
        # text, so counting them as tools too would double-count.
        schemas_in_prompt = "raw_messages" in data

    tools: list[ChatCompletionToolParam] | None = None
    tool_schema_counted = True
    logged_tools = data.get("tools")
    if not schemas_in_prompt and isinstance(logged_tools, list) and logged_tools:
        if all(_is_openai_tool_schema(t) for t in logged_tools):
            tools = logged_tools
        else:
            # Completion logs serialize tools as ToolDefinition dumps
            # (name/description only, no parameter schemas), so the tool
            # bucket cannot be reconstructed from them; mark the row
            # rather than reporting a silently wrong count.
            tool_schema_counted = False

    composition = compute_prompt_composition(
        model=_log_model(data, source), messages=messages, tools=tools
    )
    if composition is None:
        return None

    response = data.get("response")
    response_id = ""
    if isinstance(response, dict) and isinstance(response.get("id"), str):
        response_id = response["id"]
        composition = composition.model_copy(update={"response_id": response_id})

    usage_summary = data.get("usage_summary")
    if not isinstance(usage_summary, dict):
        usage_summary = {}
    usage = {
        "model": composition.model,
        "prompt_tokens": int(usage_summary.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_summary.get("completion_tokens") or 0),
        "cache_read_tokens": int(usage_summary.get("cache_read_tokens") or 0),
        "reasoning_tokens": int(usage_summary.get("reasoning_tokens") or 0),
        "context_window": int(data.get("context_window") or 0),
        "response_id": response_id,
    }

    return {
        "timestamp": float(data.get("timestamp") or 0.0),
        "source": source,
        "usage": usage,
        "composition": composition.model_dump(),
        "latency_s": data.get("latency_sec"),
        "tool_schema_counted": tool_schema_counted,
    }


def estimated_total(composition: dict[str, Any]) -> int:
    return int(sum(int(composition.get(bucket) or 0) for bucket in BUCKETS))


def build_report(root: Path) -> dict[str, Any]:
    """Ingest every log under ``root`` into rows plus a summary."""
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    root = Path(root)
    for path in iter_log_files(root):
        source = str(path.relative_to(root)) if path.is_relative_to(root) else path.name
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            skipped.append({"source": source, "reason": "unreadable or invalid JSON"})
            continue
        record = call_record_from_log(data, source)
        if record is None:
            skipped.append({"source": source, "reason": "not a countable call log"})
            continue
        rows.append(record)

    rows.sort(key=lambda r: (r["timestamp"], r["source"]))
    sequenced = [{"seq": seq, **row} for seq, row in enumerate(rows)]
    return {
        "root": str(root),
        "rows": sequenced,
        "skipped": skipped,
        "summary": _summarize(root, sequenced, skipped),
    }


def _est_provider_ratios(rows: list[dict[str, Any]]) -> list[float]:
    ratios = []
    for row in rows:
        if not row["tool_schema_counted"]:
            # The estimate is missing a bucket, so the ratio would understate.
            continue
        provider = int(row["usage"].get("prompt_tokens") or 0)
        if provider > 0:
            ratios.append(estimated_total(row["composition"]) / provider)
    return ratios


def _summarize(
    root: Path, rows: list[dict[str, Any]], skipped: list[dict[str, str]]
) -> dict[str, Any]:
    averages = {}
    for bucket in BUCKETS:
        values = [int(row["composition"].get(bucket) or 0) for row in rows]
        averages[bucket] = round(statistics.fmean(values), 1) if values else 0.0
    est_totals = [estimated_total(row["composition"]) for row in rows]
    provider_prompt = [int(row["usage"].get("prompt_tokens") or 0) for row in rows]
    ratios = _est_provider_ratios(rows)
    return {
        "root": str(root),
        "calls": len(rows),
        "skipped_files": len(skipped),
        "calls_with_uncountable_tool_schemas": sum(
            1 for row in rows if not row["tool_schema_counted"]
        ),
        "avg": averages,
        "avg_est_total": round(statistics.fmean(est_totals), 1) if est_totals else 0.0,
        "avg_provider_prompt_tokens": (
            round(statistics.fmean(provider_prompt), 1) if provider_prompt else 0.0
        ),
        "est_provider_median_ratio": (
            round(statistics.median(ratios), 3) if ratios else None
        ),
        "est_provider_ratio_calls": len(ratios),
    }


def _chart_bar(composition: dict[str, Any], tokens_per_char: float) -> str:
    parts = []
    for bucket in BUCKETS:
        chars = round(int(composition.get(bucket) or 0) / tokens_per_char)
        parts.append(_BUCKET_CHARS[bucket] * chars)
    return "".join(parts).ljust(_CHART_WIDTH)


def render_text_report(report: dict[str, Any]) -> str:
    """Render the per-call stacked bars, the trend table, and the summary."""
    rows: list[dict[str, Any]] = report["rows"]
    summary: dict[str, Any] = report["summary"]
    lines = [
        "Prompt composition per call "
        "(S=system T=tool_schema H=history L=latest, "
        f"bar width ~{_CHART_WIDTH} chars)",
    ]
    if not rows:
        lines.append("  no countable calls")
    else:
        tokens_per_char = max(
            max(estimated_total(row["composition"]) for row in rows) / _CHART_WIDTH,
            1.0,
        )
        lines.append(
            f"  {'seq':>4}  {'composition':<{_CHART_WIDTH}}  "
            f"{'est_total':>9}  {'provider':>9}"
        )
        for row in rows:
            est = estimated_total(row["composition"])
            provider = int(row["usage"].get("prompt_tokens") or 0)
            bar = _chart_bar(row["composition"], tokens_per_char)
            marker = "" if row["tool_schema_counted"] else " *"
            lines.append(f"  {row['seq']:>4}  {bar}  {est:>9}  {provider:>9}{marker}")
        if any(not row["tool_schema_counted"] for row in rows):
            lines.append(
                "  * tool schemas not reconstructable from this log "
                "(tool_schema bucket undercounted)"
            )

    lines += [
        "",
        "Trend over seq:",
        f"  {'seq':>4} | {'system':>8} | {'tool_schema':>11} | {'history':>8} | "
        f"{'latest':>8} | {'est_total':>9} | {'provider':>8} | {'ratio':>6} | "
        f"{'latency_s':>9}",
    ]
    for row in rows:
        composition = row["composition"]
        est = estimated_total(composition)
        provider = int(row["usage"].get("prompt_tokens") or 0)
        if provider > 0 and row["tool_schema_counted"]:
            ratio = f"{est / provider:.2f}"
        else:
            ratio = "-"
        latency = row["latency_s"]
        latency_s = f"{latency:.2f}" if isinstance(latency, int | float) else "-"
        lines.append(
            f"  {row['seq']:>4} | {composition['system_prompt_tokens']:>8} | "
            f"{composition['tool_schema_tokens']:>11} | "
            f"{composition['history_tokens']:>8} | "
            f"{composition['latest_message_tokens']:>8} | {est:>9} | "
            f"{provider:>8} | {ratio:>6} | {latency_s:>9}"
        )

    avg = summary["avg"]
    ratio = summary["est_provider_median_ratio"]
    lines += [
        "",
        "Summary:",
        f"  calls: {summary['calls']} "
        f"(skipped files: {summary['skipped_files']}, "
        f"uncountable tool schemas: "
        f"{summary['calls_with_uncountable_tool_schemas']})",
        f"  avg system: {avg['system_prompt_tokens']}",
        f"  avg tool_schema: {avg['tool_schema_tokens']}",
        f"  avg history: {avg['history_tokens']}",
        f"  avg latest: {avg['latest_message_tokens']}",
        f"  avg est total: {summary['avg_est_total']}  "
        f"avg provider prompt: {summary['avg_provider_prompt_tokens']}",
        f"  est/provider median ratio: "
        f"{ratio if ratio is not None else 'n/a'} "
        f"(over {summary['est_provider_ratio_calls']} calls)",
    ]
    return "\n".join(lines)


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Write the per-call JSONL rows and the summary JSON into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    calls_path = out_dir / "calls.jsonl"
    with calls_path.open("w", encoding="utf-8") as f:
        for row in report["rows"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps(report["summary"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return calls_path, summary_path


__all__ = [
    "BUCKETS",
    "build_report",
    "call_record_from_log",
    "estimated_total",
    "iter_log_files",
    "render_text_report",
    "write_report",
]
