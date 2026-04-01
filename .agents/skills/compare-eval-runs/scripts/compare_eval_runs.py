#!/usr/bin/env python3
"""Trigger, compare, and report on OpenHands evaluation runs.

Subcommands:
    trigger   Dispatch an evaluation workflow via the GitHub API
    compare   Compare two evaluation runs and produce a markdown report

Examples:
    # Trigger a swebench eval on a PR branch
    python compare_eval_runs.py trigger --sdk-ref my-branch --benchmark swebench --eval-limit 50

    # Trigger a GAIA eval on a release tag
    python compare_eval_runs.py trigger --sdk-ref v1.16.0 --benchmark gaia --eval-limit 50

    # Auto-find baseline and print comparison markdown
    python compare_eval_runs.py compare swebench/litellm_proxy-claude-sonnet-4-5-20250929/23775164157/ --auto-baseline

    # Post comparison to PR
    python compare_eval_runs.py compare swebench/.../23775164157/ --auto-baseline \\
        --post-comment --pr 2334 --repo OpenHands/software-agent-sdk
"""  # noqa: E501

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any


RESULTS_CDN = os.environ.get("RESULTS_CDN", "https://results.eval.all-hands.dev")
DASHBOARD_BASE = "https://openhands-eval-monitor.vercel.app"

SDK_REPO = "OpenHands/software-agent-sdk"
BENCHMARKS = [
    "swebench",
    "gaia",
    "swtbench",
    "commit0",
    "swebenchmultimodal",
    "terminalbench",
]
TOOL_PRESETS = ["default", "gemini", "gpt5", "planning"]
AGENT_TYPES = ["default", "acp-claude", "acp-codex"]


def fetch_json(url: str) -> dict[str, Any] | None:
    """Fetch JSON from a URL, returning None on 404."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def fetch_text(url: str) -> str | None:
    """Fetch text from a URL, returning None on 404."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    except Exception as e:
        print(f"Warning: Failed to fetch {url}: {e}", file=sys.stderr)
        return None


def parse_run_path(path: str) -> tuple[str, str, str]:
    """Parse a run path into (benchmark, model_slug, run_id).

    Accepts formats:
        swebench/litellm_proxy-claude-sonnet-4-5-20250929/23775164157/
        swebench/litellm_proxy-claude-sonnet-4-5-20250929/23775164157
    """
    parts = path.strip("/").split("/")
    if len(parts) != 3:
        raise ValueError(
            f"Invalid run path: {path!r}. Expected: benchmark/model_slug/run_id"
        )
    return parts[0], parts[1], parts[2]


def get_report(run_path: str) -> dict[str, Any] | None:
    """Fetch output.report.json for a run."""
    url = f"{RESULTS_CDN}/{run_path.strip('/')}/output.report.json"
    return fetch_json(url)


def get_params(run_path: str) -> dict[str, Any] | None:
    """Fetch metadata/params.json for a run."""
    url = f"{RESULTS_CDN}/{run_path.strip('/')}/metadata/params.json"
    return fetch_json(url)


def get_metadata_for_date(date_str: str) -> list[str]:
    """Fetch the metadata listing for a given date (YYYY-MM-DD)."""
    url = f"{RESULTS_CDN}/metadata/{date_str}.txt"
    text = fetch_text(url)
    if not text:
        return []
    return [line.strip() for line in text.strip().split("\n") if line.strip()]


def find_baseline_run(
    benchmark: str,
    model_slug: str,
    current_run_id: str,
    lookback_days: int = 14,
    current_eval_limit: int | None = None,
) -> str | None:
    """Find the most recent previous run with matching benchmark/model.

    Scans metadata files backward from today, looking for a run with the
    same benchmark and model_slug but a different (earlier) run_id.
    Prefers runs with matching eval_limit when available.

    Returns the run path or None if no baseline found.
    """
    today = datetime.now(UTC).date()
    prefix = f"{benchmark}/{model_slug}/"

    # Two-pass: first look for matching eval_limit, then any completed run
    candidates: list[tuple[str, dict[str, Any] | None]] = []

    for day_offset in range(lookback_days + 1):
        date = today - timedelta(days=day_offset)
        date_str = date.strftime("%Y-%m-%d")
        entries = get_metadata_for_date(date_str)

        for entry in reversed(entries):
            if not entry.startswith(prefix):
                continue
            _, _, run_id = parse_run_path(entry)
            if run_id == current_run_id:
                continue

            report = get_report(entry)
            if report and report.get("submitted_instances", 0) > 0:
                params = get_params(entry)
                candidates.append((entry, params))
                # Stop after finding enough candidates
                if len(candidates) >= 10:
                    break
        if len(candidates) >= 10:
            break

    if not candidates:
        return None

    # Prefer runs with matching eval_limit
    if current_eval_limit is not None:
        for path, params in candidates:
            if params and params.get("eval_limit") == current_eval_limit:
                return path

    # Fall back to most recent completed run
    return candidates[0][0]


def compute_diff(
    current: dict[str, Any],
    baseline: dict[str, Any],
    current_params: dict[str, Any] | None,
    baseline_params: dict[str, Any] | None,
) -> str:
    """Produce a markdown comparison of two eval reports."""
    # Extract key metrics
    c_resolved = current.get("resolved_instances", 0)
    b_resolved = baseline.get("resolved_instances", 0)
    c_submitted = current.get("submitted_instances", 0)
    b_submitted = baseline.get("submitted_instances", 0)
    c_total = current.get("total_instances", 0)
    b_total = baseline.get("total_instances", 0)
    c_empty = current.get("empty_patch_instances", 0)
    b_empty = baseline.get("empty_patch_instances", 0)
    c_error = current.get("error_instances", 0)
    b_error = baseline.get("error_instances", 0)

    # Eval limit from params
    c_limit = (current_params or {}).get("eval_limit", c_submitted)
    b_limit = (baseline_params or {}).get("eval_limit", b_submitted)

    # Denominators for rate calculation
    c_denom = min(c_limit, c_total) if c_total > 0 else c_limit
    b_denom = min(b_limit, b_total) if b_total > 0 else b_limit

    c_rate = (c_resolved / c_denom * 100) if c_denom else 0
    b_rate = (b_resolved / b_denom * 100) if b_denom else 0
    rate_delta = c_rate - b_rate

    # Instance-level diff
    c_resolved_ids = set(current.get("resolved_ids", []))
    b_resolved_ids = set(baseline.get("resolved_ids", []))
    gained = sorted(c_resolved_ids - b_resolved_ids)
    lost = sorted(b_resolved_ids - c_resolved_ids)

    # Delta symbol
    def delta_str(val: float | int) -> str:
        if val > 0:
            return f"+{val}"
        return str(val)

    # Build markdown
    lines: list[str] = []
    lines.append("## 📊 Evaluation Comparison")
    lines.append("")

    # Summary line
    if rate_delta > 0:
        emoji = "📈"
        delta_pp = f"+{rate_delta:.1f}"
    elif rate_delta < 0:
        emoji = "📉"
        delta_pp = f"{rate_delta:.1f}"
    else:
        emoji = "➡️"
        delta_pp = "0.0"
    lines.append(
        f"{emoji} **Success rate: {c_rate:.1f}% "
        f"({delta_pp}pp vs baseline {b_rate:.1f}%)**"
    )
    lines.append("")

    # Metadata
    c_pr = (current_params or {}).get("pr_number")
    b_pr = (baseline_params or {}).get("pr_number")
    c_commit = (current_params or {}).get("sdk_commit", "unknown")[:12]
    b_commit = (baseline_params or {}).get("sdk_commit", "unknown")[:12]
    c_run_id = (current_params or {}).get("github_run_id", "")
    b_run_id = (baseline_params or {}).get("github_run_id", "")

    lines.append("| | Current | Baseline |")
    lines.append("|---|---|---|")
    if c_run_id or b_run_id:
        lines.append(f"| **Run ID** | `{c_run_id}` | `{b_run_id}` |")
    lines.append(f"| **SDK Commit** | `{c_commit}` | `{b_commit}` |")
    if c_pr or b_pr:
        c_pr_str = f"#{c_pr}" if c_pr else "—"
        b_pr_str = f"#{b_pr}" if b_pr else "— (main)" if not b_pr else f"#{b_pr}"
        lines.append(f"| **PR** | {c_pr_str} | {b_pr_str} |")
    lines.append(
        f"| **Resolved** | {c_resolved}/{c_denom} ({c_rate:.1f}%) "
        f"| {b_resolved}/{b_denom} ({b_rate:.1f}%) |"
    )
    lines.append(f"| **Δ Resolved** | {delta_str(c_resolved - b_resolved)} | — |")
    lines.append(f"| **Empty Patches** | {c_empty} | {b_empty} |")
    lines.append(f"| **Errors** | {c_error} | {b_error} |")
    lines.append("")

    # Instance-level changes
    if gained or lost:
        lines.append("### Instance-Level Changes")
        lines.append("")

    if gained:
        lines.append(
            f"**✅ Newly resolved ({len(gained)}):** "
            + ", ".join(f"`{g}`" for g in gained[:20])
        )
        if len(gained) > 20:
            lines.append(f"  ... and {len(gained) - 20} more")
        lines.append("")

    if lost:
        lines.append(
            f"**❌ Regressions ({len(lost)}):** "
            + ", ".join(f"`{g}`" for g in lost[:20])
        )
        if len(lost) > 20:
            lines.append(f"  ... and {len(lost) - 20} more")
        lines.append("")

    if not gained and not lost and c_resolved_ids and b_resolved_ids:
        lines.append(
            "*Identical set of resolved instances — no regressions or improvements.*"
        )
        lines.append("")

    # Dashboard links
    lines.append("### 🔗 Links")
    lines.append("")
    if c_run_id:
        benchmark = (current_params or {}).get("benchmark", "swebench")
        model_slug = (
            (current_params or {})
            .get("model_name", "")
            .replace("/", "-")
            .replace(":", "-")
            .replace("@", "-")
            .replace(".", "-")
        )
        c_dash = f"{DASHBOARD_BASE}/?run={benchmark}/{model_slug}/{c_run_id}/"
        lines.append(f"- [Current run dashboard]({c_dash})")
    if b_run_id:
        benchmark = (baseline_params or {}).get("benchmark", "swebench")
        model_slug = (
            (baseline_params or {})
            .get("model_name", "")
            .replace("/", "-")
            .replace(":", "-")
            .replace("@", "-")
            .replace(".", "-")
        )
        b_dash = f"{DASHBOARD_BASE}/?run={benchmark}/{model_slug}/{b_run_id}/"
        lines.append(f"- [Baseline run dashboard]({b_dash})")
    lines.append("")

    return "\n".join(lines)


def github_api_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Make a GitHub API request. Returns parsed JSON or None for 204."""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        if resp.status == 204:
            return None
        return json.loads(resp.read().decode())


def post_github_comment(repo: str, pr_number: int, body: str, token: str) -> None:
    """Post a comment on a GitHub PR."""
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    result = github_api_request(url, token, method="POST", data={"body": body})
    if result:
        print(f"Posted comment: {result.get('html_url', 'unknown')}", file=sys.stderr)


def trigger_eval(
    token: str,
    *,
    sdk_ref: str,
    benchmark: str = "swebench",
    eval_limit: int = 50,
    model_ids: str = "",
    reason: str = "",
    repo: str = SDK_REPO,
    allow_unreleased: bool = True,
    benchmarks_branch: str = "main",
    eval_branch: str = "main",
    tool_preset: str = "default",
    agent_type: str = "default",
    instance_ids: str = "",
) -> None:
    """Dispatch an evaluation workflow via the GitHub Actions API."""
    inputs: dict[str, str] = {
        "benchmark": benchmark,
        "sdk_ref": sdk_ref,
        "eval_limit": str(eval_limit),
        "reason": reason,
        "benchmarks_branch": benchmarks_branch,
        "eval_branch": eval_branch,
        "tool_preset": tool_preset,
        "agent_type": agent_type,
        "allow_unreleased_branches": str(allow_unreleased).lower(),
    }
    if model_ids:
        inputs["model_ids"] = model_ids
    if instance_ids:
        inputs["instance_ids"] = instance_ids

    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/run-eval.yml/dispatches"
    )
    payload = {"ref": sdk_ref, "inputs": inputs}

    print(f"Dispatching eval workflow on {repo}...", file=sys.stderr)
    print(f"  benchmark:    {benchmark}", file=sys.stderr)
    print(f"  sdk_ref:      {sdk_ref}", file=sys.stderr)
    print(f"  eval_limit:   {eval_limit}", file=sys.stderr)
    print(f"  model_ids:    {model_ids or '(default)'}", file=sys.stderr)
    print(f"  tool_preset:  {tool_preset}", file=sys.stderr)
    print(f"  agent_type:   {agent_type}", file=sys.stderr)
    if instance_ids:
        print(f"  instance_ids: {instance_ids}", file=sys.stderr)
    if reason:
        print(f"  reason:       {reason}", file=sys.stderr)

    github_api_request(url, token, method="POST", data=payload)
    print("✓ Workflow dispatched successfully.", file=sys.stderr)
    print(
        f"  Monitor at: https://github.com/{repo}/actions/workflows/run-eval.yml",
        file=sys.stderr,
    )


def _require_token() -> str:
    """Return GITHUB_TOKEN or exit with error."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)
    return token


def cmd_trigger(args: argparse.Namespace) -> None:
    """Handle the 'trigger' subcommand."""
    token = _require_token()
    trigger_eval(
        token,
        sdk_ref=args.sdk_ref,
        benchmark=args.benchmark,
        eval_limit=args.eval_limit,
        model_ids=args.model_ids or "",
        reason=args.reason or "",
        repo=args.repo,
        benchmarks_branch=args.benchmarks_branch,
        eval_branch=args.eval_branch,
        tool_preset=args.tool_preset,
        agent_type=args.agent_type,
        instance_ids=args.instance_ids or "",
    )


def cmd_compare(args: argparse.Namespace) -> None:
    """Handle the 'compare' subcommand."""
    # Validate
    if args.post_comment and (not args.pr or not args.repo):
        print("ERROR: --post-comment requires --pr and --repo", file=sys.stderr)
        sys.exit(1)
    if not args.baseline and not args.auto_baseline:
        print("ERROR: Specify --baseline or --auto-baseline", file=sys.stderr)
        sys.exit(1)

    benchmark, model_slug, run_id = parse_run_path(args.current_run_path)
    print(f"Current run: {benchmark}/{model_slug}/{run_id}", file=sys.stderr)

    # Fetch current run data
    current_report = get_report(args.current_run_path)
    if not current_report:
        print(f"ERROR: No report found for {args.current_run_path}", file=sys.stderr)
        sys.exit(1)

    current_params = get_params(args.current_run_path)

    # Find baseline
    if args.baseline:
        baseline_path = args.baseline
    else:
        current_eval_limit = (
            current_params.get("eval_limit") if current_params else None
        )
        print(
            f"Searching for baseline (lookback: {args.lookback_days} days, "
            f"eval_limit: {current_eval_limit})...",
            file=sys.stderr,
        )
        baseline_path = find_baseline_run(
            benchmark, model_slug, run_id, args.lookback_days, current_eval_limit
        )

    if not baseline_path:
        print("No baseline run found. Cannot produce comparison.", file=sys.stderr)
        sys.exit(1)

    print(f"Baseline run: {baseline_path}", file=sys.stderr)

    baseline_report = get_report(baseline_path)
    if not baseline_report:
        print(f"ERROR: No report found for baseline {baseline_path}", file=sys.stderr)
        sys.exit(1)

    baseline_params = get_params(baseline_path)

    # Generate comparison
    markdown = compute_diff(
        current_report, baseline_report, current_params, baseline_params
    )
    print(markdown)

    # Post comment if requested
    if args.post_comment:
        token = _require_token()
        body = (
            markdown
            + "\n---\n"
            + "*This comparison was generated by an AI assistant "
            + "(OpenHands) on behalf of the user.*\n"
        )
        post_github_comment(args.repo, args.pr, body, token)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger, compare, and report on OpenHands evaluation runs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- trigger subcommand ---
    p_trigger = subparsers.add_parser(
        "trigger",
        help="Dispatch an evaluation workflow",
        description="Trigger an eval run via the GitHub Actions workflow_dispatch API.",
    )
    p_trigger.add_argument(
        "--sdk-ref",
        required=True,
        help="SDK branch, tag, or commit to evaluate (e.g., main, v1.16.0, my-branch)",
    )
    p_trigger.add_argument(
        "--benchmark",
        default="swebench",
        choices=BENCHMARKS,
        help="Benchmark to run (default: swebench)",
    )
    p_trigger.add_argument(
        "--eval-limit",
        type=int,
        default=50,
        help="Number of instances to evaluate (default: 50)",
    )
    p_trigger.add_argument(
        "--model-ids",
        default="",
        help=(
            "Comma-separated model IDs "
            "(see .github/run-eval/resolve_model_config.py; default: first model)"
        ),
    )
    p_trigger.add_argument("--reason", default="", help="Human-readable trigger reason")
    p_trigger.add_argument(
        "--repo",
        default=SDK_REPO,
        help=f"Repository to trigger on (default: {SDK_REPO})",
    )
    p_trigger.add_argument(
        "--benchmarks-branch",
        default="main",
        help="Benchmarks repo branch (default: main)",
    )
    p_trigger.add_argument(
        "--eval-branch",
        default="main",
        help="Evaluation repo branch (default: main)",
    )
    p_trigger.add_argument(
        "--tool-preset",
        default="default",
        choices=TOOL_PRESETS,
        help="Tool preset for file editing (default: default)",
    )
    p_trigger.add_argument(
        "--agent-type",
        default="default",
        choices=AGENT_TYPES,
        help="Agent type (default: default)",
    )
    p_trigger.add_argument(
        "--instance-ids",
        default="",
        help="Comma-separated instance IDs to evaluate (overrides eval-limit)",
    )

    # --- compare subcommand ---
    p_compare = subparsers.add_parser(
        "compare",
        help="Compare two evaluation runs",
        description="Fetch results for two eval runs and produce a diff report.",
    )
    p_compare.add_argument(
        "current_run_path",
        help="Run path (e.g., swebench/litellm_proxy-claude-.../23775164157/)",
    )
    p_compare.add_argument("--baseline", help="Explicit baseline run path")
    p_compare.add_argument(
        "--auto-baseline",
        action="store_true",
        help="Auto-find the most recent previous run as baseline",
    )
    p_compare.add_argument(
        "--lookback-days",
        type=int,
        default=14,
        help="Days to search for baseline (default: 14)",
    )
    p_compare.add_argument(
        "--post-comment",
        action="store_true",
        help="Post result as a GitHub PR comment",
    )
    p_compare.add_argument("--pr", type=int, help="PR number for commenting")
    p_compare.add_argument("--repo", help="Repository (OWNER/REPO) for commenting")

    args = parser.parse_args()

    if args.command == "trigger":
        cmd_trigger(args)
    elif args.command == "compare":
        cmd_compare(args)


if __name__ == "__main__":
    main()
