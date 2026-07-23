#!/usr/bin/env python3
"""Orchestrator for the PostHog self-healing workflow.

Three subcommands span the two-job pipeline:

* ``triage``         Job A. Query sanitized telemetry, aggregate by dedup_key,
                     maintain one tracking issue per key, apply guardrails and
                     emit the eligible list. Needs only ``requests``/``pyyaml``.
* ``remediate``      Job B. Clone the target, run a bounded agent, and leave
                     ``test.patch`` / ``fix.patch`` / ``verification.json``.
                     Imports the OpenHands SDK lazily.
* ``record-outcome`` Update tracking-issue state and pilot metrics.

Job B then runs ``verify.py`` (agent-free) and opens a draft PR only if it
passes. The privacy and privilege guarantees live in the modules this file wires
together and in ``workflow.yml``; this file only orchestrates.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import guardrails
import issue_tracker
import metrics
import repo_map
from fingerprint import Disposition, FingerprintGroup
from telemetry_source import TelemetryQueryConfig, fetch_error_groups


# --- configuration ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Config:
    days_back: int = 7
    limit: int = 1000
    min_occurrences: int = 5
    cooldown_hours: float = 24.0
    max_investigations_per_run: int = 1
    max_remediation_attempts: int = 3
    max_iterations: int = 40
    targets: tuple[repo_map.RepoTarget, ...] = repo_map.DEFAULT_TARGETS


def load_config(path: str | None) -> Config:
    """Load config.yaml, falling back to defaults if absent or yaml missing."""
    if not path or not Path(path).exists():
        return Config()
    try:
        import yaml
    except ImportError:
        return Config()
    data = yaml.safe_load(Path(path).read_text()) or {}
    tele = data.get("telemetry", {})
    thr = data.get("thresholds", {})
    agent = data.get("agent", {})
    targets = []
    for t in data.get("targets", []):
        v = t.get("verification", {})
        targets.append(
            repo_map.RepoTarget(
                module_prefix=t["module_prefix"],
                repo=t["repo"],
                sha_field=t.get("sha_field", "build_git_sha"),
                verification=repo_map.VerificationProfile(
                    runner=v.get("runner", "pytest"),
                    test_root=v.get("test_root", "tests/"),
                ),
            )
        )
    return Config(
        days_back=int(tele.get("days_back", 7)),
        limit=int(tele.get("limit", 1000)),
        min_occurrences=int(thr.get("min_occurrences", 5)),
        cooldown_hours=float(thr.get("cooldown_hours", 24)),
        max_investigations_per_run=int(thr.get("max_investigations_per_run", 1)),
        max_remediation_attempts=int(thr.get("max_remediation_attempts", 3)),
        max_iterations=int(agent.get("max_iterations", 40)),
        targets=tuple(targets) if targets else repo_map.DEFAULT_TARGETS,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _emit_output(name: str, value: str) -> None:
    """Write a GitHub Actions job output (and echo for local runs)."""
    out = os.getenv("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"{name}<<__EOF__\n{value}\n__EOF__\n")
    print(f"::output {name}={value}")


# --- triage (Job A) -----------------------------------------------------------


def _candidate_record(group: FingerprintGroup, elig: repo_map.Eligibility) -> dict:
    """The PII-free record passed from triage to remediation.

    Validated tokens only -- safe to place in a job output and a log.
    """
    ctx = group.to_prompt_context()
    return {
        "dedup_key": group.dedup_key,
        "error_class": ctx["error_class"],
        "error_category": ctx["error_category"],
        "error_origin_module": ctx["error_origin_module"],
        "occurrence_count": group.count,
        "example_fingerprint": sorted(group.fingerprints)[0]
        if group.fingerprints
        else "",
        "repo": elig.target.repo if elig.target else "",
        "base_sha": elig.base_sha,
    }


def cmd_triage(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    env = os.environ
    posthog_key = env.get("POSTHOG_API_KEY", "")
    project_id = env.get("POSTHOG_PROJECT_ID", "")
    host = env.get("POSTHOG_HOST") or "us.posthog.com"
    github_token = env.get("GITHUB_TOKEN", "")
    issue_repo = args.issue_repo
    metrics_path = Path(args.metrics_file) if args.metrics_file else None
    run_id = env.get("GITHUB_RUN_ID", "local")
    kill_on = guardrails.kill_switch_enabled(env)

    if not (posthog_key and project_id):
        print("❌ POSTHOG_API_KEY and POSTHOG_PROJECT_ID are required", file=sys.stderr)
        return 1

    groups = fetch_error_groups(
        TelemetryQueryConfig(
            api_key=posthog_key,
            project_id=project_id,
            host=host,
            days_back=config.days_back,
            limit=config.limit,
        )
    )
    print(f"🔎 {len(groups)} fingerprint group(s) from telemetry")

    candidates: list[dict] = []
    for group in groups:
        elig = repo_map.evaluate(
            group, targets=config.targets, min_count=config.min_occurrences
        )

        # Maintain the tracking issue for every observed group (eligible or not).
        issue = None
        state = issue_tracker.SelfHealState()
        if github_token and not args.dry_run:
            issue = issue_tracker.find_issue(issue_repo, group.dedup_key, github_token)
            if issue is None:
                state = issue_tracker.SelfHealState(disposition=Disposition.NEW.value)
                created = issue_tracker.create_issue(
                    issue_repo, group, state, github_token
                )
                issue = created
                _metric(metrics_path, run_id, group, "issue_created")
            else:
                state = issue_tracker.SelfHealState.parse(issue.get("body"))
                issue_tracker.update_issue(
                    issue_repo,
                    issue["number"],
                    issue_tracker.render_body(group, state),
                    github_token,
                )
                _metric(metrics_path, run_id, group, "issue_updated")

        if not elig.eligible:
            print(f"  · {group.dedup_key} ineligible: {elig.reason}")
            _metric(metrics_path, run_id, group, "skipped_ineligible", elig.reason)
            continue
        if not kill_on:
            print(f"  · {group.dedup_key} eligible but kill switch is OFF")
            continue
        if guardrails.is_terminal(state):
            print(f"  · {group.dedup_key} skipped: terminal ({state.disposition})")
            continue
        if guardrails.in_cooldown(state, _now(), config.cooldown_hours):
            print(f"  · {group.dedup_key} skipped: in cooldown")
            _metric(metrics_path, run_id, group, "skipped_cooldown")
            continue
        if state.attempt_count >= config.max_remediation_attempts:
            print(f"  · {group.dedup_key} skipped: attempts exhausted")
            continue

        candidates.append(_candidate_record(group, elig))

    kept, dropped = guardrails.select_within_budget(
        candidates, config.max_investigations_per_run
    )
    if dropped:
        print(
            f"⏳ rate limit: {dropped} eligible fingerprint(s) deferred to a later run"
        )

    _emit_output("eligible", json.dumps(kept))
    _emit_output("has_eligible", "true" if kept else "false")
    print(f"✅ {len(kept)} fingerprint(s) queued for remediation")
    return 0


def _metric(
    path: Path | None,
    run_id: str,
    group: FingerprintGroup,
    outcome: str,
    detail: str = "",
) -> None:
    if path is None:
        return
    metrics.record(
        path,
        metrics.MetricRecord(
            run_id=run_id,
            occurred_at=_now().isoformat(),
            dedup_key=group.dedup_key,
            error_class=group.to_prompt_context()["error_class"],  # type: ignore[arg-type]
            outcome=outcome,  # type: ignore[arg-type]
            detail=detail,
        ),
    )


# --- remediate (Job B) --------------------------------------------------------


def cmd_remediate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    patch_dir = workdir / "artifacts"
    patch_dir.mkdir(exist_ok=True)

    target = repo_map.resolve_target(args.origin_module, config.targets)
    test_root = target.verification.test_root if target else "tests/"
    test_path = f"{test_root}regressions/test_selfheal_{args.dedup_key}.py"

    context = {
        "dedup_key": args.dedup_key,
        "error_class": args.error_class,
        "error_category": args.error_category,
        "error_origin_module": args.origin_module,
        "is_first_party": True,
        "occurrence_count": args.count,
        "affected_releases": [args.base_sha],
        "example_fingerprints": [args.example_fingerprint]
        if args.example_fingerprint
        else [],
        "target_repo": args.repo,
        "base_sha": args.base_sha,
        "test_path": test_path,
        "test_root": test_root,
        "patch_dir": str(patch_dir),
        "verification_json_path": str(patch_dir / "verification.json"),
    }

    prompt = _render_prompt(context)
    return _run_agent(prompt, workdir, config, args.repo, args.base_sha)


def _render_prompt(context: dict) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(Path(__file__).parent)))
    return env.get_template("debug_prompt.jinja").render(**context)


def _run_agent(
    prompt: str, workdir: Path, config: Config, repo: str, base_sha: str
) -> int:
    """Clone read-only and run a bounded OpenHands investigation."""
    import subprocess

    from pydantic import SecretStr

    from openhands.sdk import (
        LLM,
        Agent,
        Conversation,
        Message,
        TextContent,
        get_logger,
    )
    from openhands.sdk.tool import Tool, register_tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    logger = get_logger(__name__)
    token = os.getenv("GITHUB_TOKEN", "")
    repo_dir = workdir / repo.split("/")[-1]
    if not repo_dir.exists():
        clone_url = (
            f"https://{token}@github.com/{repo}.git"
            if token
            else f"https://github.com/{repo}.git"
        )
        subprocess.run(
            ["git", "clone", "--no-tags", clone_url, str(repo_dir)], check=True
        )
        subprocess.run(["git", "-C", str(repo_dir), "checkout", base_sha], check=True)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("❌ LLM_API_KEY is required for remediation", file=sys.stderr)
        return 1
    llm = LLM(
        model=os.getenv("LLM_MODEL", "claude-sonnet-5"),
        base_url=os.getenv("LLM_BASE_URL"),
        api_key=SecretStr(api_key),
    )

    register_tool("TerminalTool", TerminalTool)
    register_tool("FileEditorTool", FileEditorTool)
    register_tool("TaskTrackerTool", TaskTrackerTool)
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name="TerminalTool"),
            Tool(name="FileEditorTool"),
            Tool(name="TaskTrackerTool"),
        ],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(repo_dir),
        max_iteration_per_run=config.max_iterations,
    )
    conversation.send_message(Message(role="user", content=[TextContent(text=prompt)]))
    try:
        conversation.run()
    finally:
        logger.info("Closing remediation conversation")
        conversation.close()
    return 0


# --- record-outcome -----------------------------------------------------------


def cmd_record_outcome(args: argparse.Namespace) -> int:
    token = os.getenv("GITHUB_TOKEN", "")
    metrics_path = Path(args.metrics_file) if args.metrics_file else None
    run_id = os.getenv("GITHUB_RUN_ID", "local")

    resp = issue_tracker.find_issue(args.issue_repo, args.dedup_key, token)
    if resp is None:
        print("⚠️  tracking issue not found; nothing to record", file=sys.stderr)
        return 0
    number = resp["number"]
    state = issue_tracker.SelfHealState.parse(resp.get("body"))

    state.last_remediation_at = _now().isoformat()
    if args.outcome == "verified_pr_opened":
        state.disposition = Disposition.PR_OPEN.value
    else:
        state.attempt_count += 1
        state.disposition = Disposition.INVESTIGATING.value

    issue_tracker.update_issue(
        args.issue_repo,
        number,
        issue_tracker.replace_state_in_body(resp.get("body") or "", state),
        token,
    )
    note = args.detail or args.outcome
    issue_tracker.comment(
        args.issue_repo,
        number,
        f"**Self-heal update:** `{args.outcome}` — {note}"
        + (f"\n\nDraft PR: {args.pr_url}" if args.pr_url else ""),
        token,
    )
    if metrics_path is not None:
        metrics.record(
            metrics_path,
            metrics.MetricRecord(
                run_id=run_id,
                occurred_at=_now().isoformat(),
                dedup_key=args.dedup_key,
                error_class=args.error_class or "unknown",
                outcome=args.outcome,  # type: ignore[arg-type]
                detail=note,
            ),
        )
    print(f"📝 recorded {args.outcome} on issue #{number}")
    return 0


# --- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("triage", help="Job A: aggregate telemetry, track issues")
    t.add_argument("--config", default="config.yaml")
    t.add_argument("--issue-repo", required=True)
    t.add_argument("--metrics-file")
    t.add_argument("--dry-run", action="store_true")
    t.set_defaults(func=cmd_triage)

    r = sub.add_parser("remediate", help="Job B: run the bounded agent")
    r.add_argument("--config", default="config.yaml")
    r.add_argument("--dedup-key", required=True)
    r.add_argument("--repo", required=True)
    r.add_argument("--base-sha", required=True)
    r.add_argument("--error-class", required=True)
    r.add_argument("--error-category", default="unknown")
    r.add_argument("--origin-module", required=True)
    r.add_argument("--count", type=int, default=0)
    r.add_argument("--example-fingerprint", default="")
    r.add_argument("--workdir", default="./workspace")
    r.set_defaults(func=cmd_remediate)

    ro = sub.add_parser("record-outcome", help="update issue state + metrics")
    ro.add_argument("--issue-repo", required=True)
    ro.add_argument("--dedup-key", required=True)
    ro.add_argument("--outcome", required=True)
    ro.add_argument("--error-class", default="")
    ro.add_argument("--detail", default="")
    ro.add_argument("--pr-url", default="")
    ro.add_argument("--metrics-file")
    ro.set_defaults(func=cmd_record_outcome)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
