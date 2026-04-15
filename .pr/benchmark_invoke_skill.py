"""Benchmark: progressive-disclosure skill recognition rate.

Measures how often an agent invokes a clearly-relevant progressive-disclosure
skill when only `<available_skills>` metadata (name / description / location)
is visible in the system prompt.

A prompt counts as a HIT when, during the conversation, the agent either:
  - calls `Read` on the expected SKILL.md location (current affordance), or
  - calls `invoke_skill(name=...)` with the expected skill name
    (the affordance proposed in issue #2824).

Both paths are counted so the same script produces a baseline number today
and an uplift number once issue #2824 is implemented — just rerun it.

Usage:
    LLM_API_KEY=... python .pr/benchmark_invoke_skill.py
    # optional:
    #   LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
    #   BENCHMARK_TRIALS=3          # repeat each prompt N times, report mean
    #   BENCHMARK_PROMPTS=frontend-design,pdf-analyst   # subset
"""

from __future__ import annotations

import logging
import os
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    AgentContext,
    Conversation,
    Event,
)
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
logging.getLogger("openhands").setLevel(logging.ERROR)
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.ERROR)


SKILLS_DIR = Path(__file__).parent / "skills"

# 5 skills x 4 prompts = 20 prompts (mirrors the 20-prompt eval in #2587).
PROMPTS: dict[str, list[str]] = {
    "frontend-design": [
        "Make a pretty landing page that explains what open source software is.",
        "Design a clean hero section for a startup homepage in HTML/CSS.",
        "Help me pick fonts, colors, and spacing for a minimalist portfolio site.",
        "Style this signup form so it looks professional on mobile and desktop.",
    ],
    "pdf-analyst": [
        "I have a PDF of a scientific paper — extract the abstract and references.",
        "Pull all tables out of this 200-page PDF report into CSV.",
        "Summarize the contents of invoice.pdf.",
        "The PDF is scanned — how do I get the text out?",
    ],
    "git-archeologist": [
        "Find the commit that introduced the string `MAX_RETRIES = 5` in this repo.",
        "Who last changed line 42 of src/server.py, ignoring whitespace-only edits?",
        "A test started failing in the last month — help me bisect to the bad commit.",
        "Explain why this function was refactored, based on git history.",
    ],
    "sql-optimizer": [
        "This Postgres query takes 40 seconds — help me make it faster.",
        "Should I add an index on (user_id, created_at) for this query?",
        "Rewrite this correlated subquery to be faster on MySQL.",
        "Explain this EXPLAIN ANALYZE output and suggest optimizations.",
    ],
    "data-viz": [
        "Plot monthly revenue vs. cost from this CSV using matplotlib.",
        "Make a small-multiples chart comparing 8 experiment arms over time.",
        "Pick a colorblind-safe palette for a 5-series line chart.",
        "Visualize this wide dataframe of sensor readings as a dashboard.",
    ],
}


def load_skills() -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    for d in sorted(SKILLS_DIR.iterdir()):
        skill_md = d / "SKILL.md"
        if skill_md.exists():
            s = Skill.load(skill_md)
            out[s.name] = s
    return out


def build_agent() -> tuple[Agent, list[Skill]]:
    api_key = os.environ["LLM_API_KEY"]
    model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")
    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )
    tools = [Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
    skills = list(load_skills().values())
    agent_context = AgentContext(skills=skills)
    return Agent(llm=llm, tools=tools, agent_context=agent_context), skills


def _invoked_expected_skill(events: list[Event], skill: Skill) -> bool:
    """
    True if the agent invoked `skill` via
    - Read(<location>) or
    - invoke_skill(name=...).
    """
    expected_location = (skill.source or "").strip()
    for e in events:
        if not isinstance(e, ActionEvent):
            continue
        name = e.tool_name
        args = e.tool_call.arguments if hasattr(e.tool_call, "arguments") else {}
        if isinstance(args, str):
            # Some tool_call shapes carry a JSON string; be defensive.
            import json

            try:
                args = json.loads(args)
            except Exception:
                args = {}

        if name == "invoke_skill" and args.get("name") == skill.name:
            return True

        if name in ("Read", "str_replace_editor", "file_editor"):
            path = args.get("path") or args.get("file") or args.get("command_arg") or ""
            if expected_location and expected_location in str(path):
                return True
    return False


def run_trial(skill: Skill, prompt: str) -> bool:
    agent, _ = build_agent()
    events: list[Event] = []
    conv = Conversation(
        agent=agent,
        callbacks=[events.append],
        workspace=os.getcwd(),
        visualizer=None,
    )
    conv.send_message(prompt)
    conv.run()
    return _invoked_expected_skill(events, skill)


def _safe_trial(skill: Skill, prompt: str) -> bool | None:
    try:
        return run_trial(skill, prompt)
    except Exception:
        return None


def main() -> None:
    trials = int(os.getenv("BENCHMARK_TRIALS", "1"))
    workers = int(os.getenv("BENCHMARK_WORKERS", "8"))
    subset_env = os.getenv("BENCHMARK_PROMPTS")
    subset = set(subset_env.split(",")) if subset_env else None

    skills = load_skills()
    missing = set(PROMPTS) - set(skills)
    assert not missing, f"Missing SKILL.md for: {missing}"

    # Build (skill, prompt, trial_idx) work items and run them all concurrently.
    jobs: list[tuple[str, str, int]] = []
    for skill_name, prompts in PROMPTS.items():
        if subset and skill_name not in subset:
            continue
        for prompt in prompts:
            for t in range(trials):
                jobs.append((skill_name, prompt, t))

    # key = (skill_name, prompt) -> list[bool | None]
    outcomes: dict[tuple[str, str], list[bool | None]] = defaultdict(list)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_safe_trial, skills[s], p): (s, p) for (s, p, _) in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            outcomes[key].append(fut.result())

    print("=== per-prompt ===")
    per_skill: dict[str, list[float]] = defaultdict(list)
    errors = 0
    for skill_name, prompts in PROMPTS.items():
        if subset and skill_name not in subset:
            continue
        for prompt in prompts:
            res = outcomes[(skill_name, prompt)]
            ok = [r for r in res if r is not None]
            errors += len(res) - len(ok)
            rate = (sum(ok) / len(ok)) if ok else 0.0
            per_skill[skill_name].append(rate)
            print(f"  [{skill_name:<18}] {rate:4.0%}  {prompt}")

    print("\n=== summary ===")
    overall: list[float] = []
    for skill_name, rates in per_skill.items():
        mean = statistics.mean(rates) if rates else 0.0
        overall.extend(rates)
        print(f"  {skill_name:<18}  recognition={mean:5.1%}  ({len(rates)} prompts)")
    total_mean = statistics.mean(overall) if overall else 0.0
    print(
        f"\n  OVERALL            recognition={total_mean:5.1%}  "
        f"({len(overall)} prompts x {trials} trials, {errors} errors)"
    )


if __name__ == "__main__":
    main()
