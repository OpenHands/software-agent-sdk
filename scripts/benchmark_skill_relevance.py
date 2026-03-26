#!/usr/bin/env python3
"""Benchmark skill relevance across the standard Agent and Claude ACP.

This script copies a curated set of skills from an OpenHands/extensions checkout
into a benchmark workspace under `.claude/skills`, runs 20 prompt cases against
one or both agent backends, records trajectories, and summarizes whether the
expected skill was surfaced and/or accessed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext
from openhands.sdk.agent import ACPAgent
from openhands.sdk.context.skills import Skill, load_skills_from_dir
from openhands.sdk.conversation import Conversation
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


@dataclass(frozen=True)
class PromptCase:
    skill_name: str
    prompt: str


PROMPT_CASES: list[PromptCase] = [
    PromptCase(
        skill_name="frontend-design",
        prompt="Make a pretty frontend that describes what open source is.",
    ),
    PromptCase(
        skill_name="docker",
        prompt="Help me package this Python app into a Docker image.",
    ),
    PromptCase(
        skill_name="kubernetes",
        prompt="I want to try this service on a tiny local Kubernetes cluster.",
    ),
    PromptCase(
        skill_name="datadog",
        prompt="Can you help me investigate a Datadog latency alert?",
    ),
    PromptCase(
        skill_name="github",
        prompt=(
            "Take a look at the failing pull request on GitHub and tell me "
            "what's wrong."
        ),
    ),
    PromptCase(
        skill_name="gitlab",
        prompt="Can you put together a merge request for this branch in GitLab?",
    ),
    PromptCase(
        skill_name="linear",
        prompt=(
            "Please file a Linear ticket for this regression with reproduction steps."
        ),
    ),
    PromptCase(
        skill_name="notion",
        prompt="Write up our release checklist in Notion.",
    ),
    PromptCase(
        skill_name="discord",
        prompt="Build a Discord slash command that shows the current server stats.",
    ),
    PromptCase(
        skill_name="ssh",
        prompt="SSH into the Linux host and check the app logs for me.",
    ),
    PromptCase(
        skill_name="uv",
        prompt="Set this Python project up with uv and get the dependencies synced.",
    ),
    PromptCase(
        skill_name="jupyter",
        prompt="Please clean up this notebook and run the cells in order.",
    ),
    PromptCase(
        skill_name="pdflatex",
        prompt="Turn this LaTeX paper into a PDF and tell me what breaks.",
    ),
    PromptCase(
        skill_name="vercel",
        prompt="Can you get this web app deployed on Vercel?",
    ),
    PromptCase(
        skill_name="releasenotes",
        prompt="Please draft release notes since our last release tag.",
    ),
    PromptCase(
        skill_name="security",
        prompt="Give this authentication flow a security review.",
    ),
    PromptCase(
        skill_name="code-review",
        prompt="Give me a careful code review of this patch.",
    ),
    PromptCase(
        skill_name="skill-creator",
        prompt="Help me design a reusable skill for database migration plans.",
    ),
    PromptCase(
        skill_name="readiness-report",
        prompt="How ready is this repo for autonomous coding agents?",
    ),
    PromptCase(
        skill_name="openhands-api",
        prompt="Show me how to start a fresh OpenHands Cloud conversation from code.",
    ),
]

GUIDED_BENCHMARK_SUFFIX = (
    "\n\nBefore you answer, inspect any relevant guidance files from the "
    "workspace. Then briefly explain your first step and stop there."
)


def compose_benchmark_prompt(case: PromptCase, prompt_style: str) -> str:
    if prompt_style == "guided":
        return case.prompt + GUIDED_BENCHMARK_SUFFIX
    return case.prompt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        choices=("openhands", "claude-acp", "both"),
        default="both",
        help="Agent backend(s) to benchmark.",
    )
    parser.add_argument(
        "--extensions-dir",
        type=Path,
        default=Path("/workspace/extensions"),
        help="Local checkout of OpenHands/extensions.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".pr/skill-relevance-benchmark"),
        help="Directory for benchmark outputs.",
    )
    parser.add_argument(
        "--workspace-dir",
        type=Path,
        default=None,
        help="Workspace used for the benchmark. Defaults under output-dir.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of prompt cases to execute.",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=4,
        help="Conversation iteration cap for the standard OpenHands agent.",
    )
    parser.add_argument(
        "--prompt-style",
        choices=("natural", "guided"),
        default="natural",
        help=(
            "Prompt style for the benchmark. 'natural' uses plain user requests "
            "to mirror the original skill-triggering issue; 'guided' appends an "
            "instruction to inspect relevant guidance files."
        ),
    )
    parser.add_argument(
        "--acp-model",
        default=os.environ.get("ACP_MODEL"),
        help="Optional model override for the Claude ACP agent.",
    )
    parser.add_argument(
        "--acp-prompt-timeout",
        type=float,
        default=300.0,
        help="Timeout for a single Claude ACP prompt call, in seconds.",
    )
    return parser.parse_args()


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def resolve_workspace_dir(args: argparse.Namespace) -> Path:
    if args.workspace_dir is not None:
        return args.workspace_dir
    return args.output_dir / "workspace"


def prepare_workspace(
    workspace_dir: Path, extensions_dir: Path, skill_names: list[str]
) -> Path:
    skills_root = extensions_dir / "skills"
    if not skills_root.exists():
        raise FileNotFoundError(f"Extensions skills directory not found: {skills_root}")

    if workspace_dir.exists():
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True)

    benchmark_readme = workspace_dir / "README.md"
    benchmark_readme.write_text(
        "# Skill relevance benchmark workspace\n\n"
        "This workspace is generated by scripts/benchmark_skill_relevance.py.\n"
    )

    target_skills_dir = workspace_dir / ".claude" / "skills"
    target_skills_dir.mkdir(parents=True)
    for skill_name in skill_names:
        source = skills_root / skill_name
        target = target_skills_dir / skill_name
        if not source.exists():
            raise FileNotFoundError(f"Skill directory not found: {source}")
        shutil.copytree(source, target)

    subprocess.run(["git", "init", "-q"], cwd=workspace_dir, check=True)
    return target_skills_dir


def load_workspace_skills(skills_dir: Path) -> list[Skill]:
    repo_skills, knowledge_skills, agent_skills = load_skills_from_dir(skills_dir)
    return [
        *repo_skills.values(),
        *knowledge_skills.values(),
        *agent_skills.values(),
    ]


def build_llm() -> LLM:
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable is required")
    model = os.environ.get("LLM_MODEL")
    if not model:
        raise RuntimeError("LLM_MODEL environment variable is required")
    return LLM(
        usage_id="skill-relevance-benchmark",
        model=model,
        base_url=os.environ.get("LLM_BASE_URL"),
        api_key=SecretStr(api_key),
    )


def build_openhands_agent(skills_dir: Path) -> Agent:
    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
    ]
    skills = load_workspace_skills(skills_dir)
    return Agent(
        llm=build_llm(),
        tools=tools,
        agent_context=AgentContext(skills=skills),
    )


def configure_anthropic_env() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("LLM_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = os.environ["LLM_API_KEY"]

    llm_base_url = os.environ.get("LLM_BASE_URL")
    if llm_base_url:
        os.environ.setdefault("ANTHROPIC_BASE_URL", llm_base_url)
    else:
        os.environ.setdefault(
            "ANTHROPIC_BASE_URL", "https://llm-proxy.app.all-hands.dev/"
        )


def build_claude_acp_agent(args: argparse.Namespace) -> ACPAgent:
    configure_anthropic_env()
    return ACPAgent(
        acp_command=["npx", "-y", "@zed-industries/claude-agent-acp"],
        acp_model=args.acp_model,
        acp_prompt_timeout=args.acp_prompt_timeout,
    )


def event_to_dict(event: Any) -> dict[str, Any]:
    if hasattr(event, "model_dump_json"):
        payload = json.loads(event.model_dump_json(exclude_none=True))
    else:
        payload = {"repr": repr(event)}
    payload["event_class"] = type(event).__name__
    payload["event_module"] = type(event).__module__
    return payload


def extract_relevant_hint(payload: dict[str, Any], skill_name: str) -> bool:
    if payload.get("event_class") != "MessageEvent":
        return False
    if payload.get("source") != "user":
        return False
    extended_content = payload.get("extended_content") or []
    serialized = json.dumps(extended_content, ensure_ascii=False)
    return (
        "<RELEVANT_SKILLS>" in serialized and f"<name>{skill_name}</name>" in serialized
    )


def extract_non_prompt_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for payload in events:
        if payload.get("event_class") == "SystemPromptEvent":
            continue
        if (
            payload.get("event_class") == "MessageEvent"
            and payload.get("source") == "user"
        ):
            continue
        filtered.append(payload)
    return filtered


def analyze_case(
    *,
    case: PromptCase,
    benchmark_prompt: str,
    prompt_style: str,
    event_payloads: list[dict[str, Any]],
    skill_path: Path,
    elapsed_seconds: float,
    error: str | None,
) -> dict[str, Any]:
    activated = sorted(
        {
            skill_name
            for payload in event_payloads
            if payload.get("event_class") == "MessageEvent"
            and payload.get("source") == "user"
            for skill_name in payload.get("activated_skills", [])
        }
    )
    suggested = any(
        extract_relevant_hint(payload, case.skill_name) for payload in event_payloads
    )

    non_prompt_blob = json.dumps(
        extract_non_prompt_events(event_payloads),
        ensure_ascii=False,
        sort_keys=True,
    )
    skill_path_str = str(skill_path)
    skill_path_suffix = f"/{case.skill_name}/SKILL.md"
    path_accessed = (
        skill_path_str in non_prompt_blob or skill_path_suffix in non_prompt_blob
    )

    reasons: list[str] = []
    if case.skill_name in activated:
        reasons.append("activated_skills")
    if suggested:
        reasons.append("relevant_skill_hint")
    if path_accessed:
        reasons.append("skill_path_in_tool_trajectory")

    agent_messages = [
        payload
        for payload in event_payloads
        if payload.get("event_class") == "MessageEvent"
        and payload.get("source") == "agent"
    ]
    final_response = ""
    if agent_messages:
        final_response = json.dumps(
            agent_messages[-1].get("llm_message", {}), ensure_ascii=False
        )

    return {
        "skill_name": case.skill_name,
        "prompt": case.prompt,
        "benchmark_prompt": benchmark_prompt,
        "prompt_style": prompt_style,
        "skill_path": skill_path_str,
        "activated_skills": activated,
        "suggested": suggested,
        "path_accessed": path_accessed,
        "skill_accessed": case.skill_name in activated or path_accessed,
        "reasons": reasons,
        "event_count": len(event_payloads),
        "elapsed_seconds": round(elapsed_seconds, 2),
        "error": error,
        "final_response_excerpt": final_response[:500],
    }


def run_case(
    *,
    agent_kind: str,
    case: PromptCase,
    args: argparse.Namespace,
    workspace_dir: Path,
    skills_dir: Path,
) -> dict[str, Any]:
    if agent_kind == "openhands":
        agent = build_openhands_agent(skills_dir)
    elif agent_kind == "claude-acp":
        agent = build_claude_acp_agent(args)
    else:
        raise ValueError(f"Unsupported agent kind: {agent_kind}")

    benchmark_prompt = compose_benchmark_prompt(case, args.prompt_style)
    error: str | None = None
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace_dir),
        visualizer=None,
        max_iteration_per_run=args.max_iterations,
    )
    start = time.monotonic()
    try:
        conversation.send_message(benchmark_prompt)
        conversation.run()
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        log(f"{agent_kind} failed for {case.skill_name}: {error}")
    finally:
        close = getattr(agent, "close", None)
        if callable(close):
            close()
    elapsed_seconds = time.monotonic() - start

    event_payloads = [event_to_dict(event) for event in conversation.state.events]
    skill_path = skills_dir / case.skill_name / "SKILL.md"
    result = analyze_case(
        case=case,
        benchmark_prompt=benchmark_prompt,
        prompt_style=args.prompt_style,
        event_payloads=event_payloads,
        skill_path=skill_path,
        elapsed_seconds=elapsed_seconds,
        error=error,
    )
    result["agent_kind"] = agent_kind
    result["events"] = event_payloads
    return result


def write_case_artifact(output_dir: Path, result: dict[str, Any]) -> None:
    case_dir = output_dir / result["agent_kind"]
    case_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{result['skill_name']}.json"
    (case_dir / file_name).write_text(json.dumps(result, indent=2, ensure_ascii=False))


def summarize_agent_results(
    agent_kind: str, case_results: list[dict[str, Any]]
) -> dict[str, Any]:
    total = len(case_results)
    accessed = sum(1 for result in case_results if result["skill_accessed"])
    suggested = sum(1 for result in case_results if result["suggested"])
    errors = [
        {
            "skill_name": result["skill_name"],
            "error": result["error"],
        }
        for result in case_results
        if result["error"]
    ]
    return {
        "agent_kind": agent_kind,
        "total_cases": total,
        "skills_accessed": accessed,
        "skills_suggested": suggested,
        "access_rate": round(accessed / total, 3) if total else 0.0,
        "suggestion_rate": round(suggested / total, 3) if total else 0.0,
        "errors": errors,
        "cases": [
            {key: value for key, value in result.items() if key != "events"}
            for result in case_results
        ],
    }


def render_markdown_report(
    agent_summaries: list[dict[str, Any]],
    prompt_style: str,
) -> str:
    lines = ["# Skill relevance benchmark", ""]
    lines.append(f"Prompt style: `{prompt_style}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Agent | Accessed | Suggested | Total | Access rate |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for summary in agent_summaries:
        summary_row = (
            "| {agent_kind} | {skills_accessed} | {skills_suggested} | "
            "{total_cases} | {access_rate:.1%} |"
        ).format(**summary)
        lines.append(summary_row)
    for summary in agent_summaries:
        lines.append("")
        lines.append(f"## {summary['agent_kind']}")
        lines.append("")
        lines.append("| Skill | Accessed | Suggested | Reasons | Error |")
        lines.append("| --- | --- | --- | --- | --- |")
        for case in summary["cases"]:
            reasons = ", ".join(case["reasons"]) or "-"
            error = case["error"] or "-"
            case_row = (
                f"| {case['skill_name']} | {case['skill_accessed']} | "
                f"{case['suggested']} | {reasons} | {error} |"
            )
            lines.append(case_row)
    lines.append("")
    return "\n".join(lines)


def write_summary(
    output_dir: Path,
    prompt_cases: list[PromptCase],
    prompt_style: str,
    agent_summaries: list[dict[str, Any]],
) -> None:
    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt_style": prompt_style,
        "prompt_cases": [asdict(case) for case in prompt_cases],
        "agents": agent_summaries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    (output_dir / "comparison.md").write_text(
        render_markdown_report(agent_summaries, prompt_style)
    )


def get_agent_kinds(agent_arg: str) -> list[str]:
    if agent_arg == "both":
        return ["openhands", "claude-acp"]
    return [agent_arg]


def main() -> None:
    args = parse_args()
    cases = PROMPT_CASES[: args.limit]
    if len(cases) < args.limit:
        log(f"Requested {args.limit} cases but only {len(cases)} are defined.")
    skill_names = [case.skill_name for case in cases]

    workspace_dir = resolve_workspace_dir(args)
    skills_dir = prepare_workspace(workspace_dir, args.extensions_dir, skill_names)
    output_dir = args.output_dir
    log(f"Prepared benchmark workspace at {workspace_dir}")

    agent_summaries: list[dict[str, Any]] = []
    for agent_kind in get_agent_kinds(args.agent):
        log(f"Starting benchmark for {agent_kind}")
        case_results: list[dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            log(f"[{agent_kind}] Case {index}/{len(cases)}: {case.skill_name}")
            result = run_case(
                agent_kind=agent_kind,
                case=case,
                args=args,
                workspace_dir=workspace_dir,
                skills_dir=skills_dir,
            )
            case_results.append(result)
            write_case_artifact(output_dir, result)
            agent_summary = summarize_agent_results(agent_kind, case_results)
            partial_summaries = [*agent_summaries, agent_summary]
            write_summary(output_dir, cases, args.prompt_style, partial_summaries)
            log(
                f"[{agent_kind}] {case.skill_name}: "
                f"accessed={result['skill_accessed']} "
                f"suggested={result['suggested']} "
                f"reasons={result['reasons']}"
            )
        agent_summaries.append(summarize_agent_results(agent_kind, case_results))
        write_summary(output_dir, cases, args.prompt_style, agent_summaries)
        log(f"Completed benchmark for {agent_kind}")

    log(f"Wrote benchmark comparison to {output_dir}")


if __name__ == "__main__":
    main()
