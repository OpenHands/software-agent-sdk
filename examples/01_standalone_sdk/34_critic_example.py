"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates the SDK's critic refinement module in two parts:

**Part 1 — Refinement API walkthrough** (no server/LLM needed):
Shows ``IterativeRefinementConfig.evaluate()`` and APIBasedCritic's
critic-specific refinement hooks with a synthetic ``CriticResult``, so you can
see how the SDK decides whether to refine and what follow-up prompt it
generates.

**Part 2 — End-to-end with APIBasedCritic** (requires critic server + LLM):
Wires an ``APIBasedCritic`` with the same config and lets
``Conversation.run()`` drive the refinement loop automatically.

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-
configured using the same base_url with /vllm suffix and "critic" as the
model name.
"""

import os
import re
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import (
    APIBasedCritic,
    CriticResult,
    IterativeRefinementConfig,
)
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "0.7"))
ISSUE_THRESHOLD = float(os.getenv("CRITIC_ISSUE_THRESHOLD", "0.75"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


# ---------------------------------------------------------------------------
# APIBasedCritic: auto-configure for All-Hands LLM proxy
# ---------------------------------------------------------------------------
def get_default_critic(llm: LLM) -> APIBasedCritic | None:
    """Return an APIBasedCritic when behind the All-Hands LLM proxy.

    The proxy exposes a ``/vllm`` critic endpoint with model name ``"critic"``.
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )


# ===================================================================
# Part 1: Refinement API walkthrough (runs without server or LLM)
# ===================================================================
print("=" * 70)
print("Part 1 — SDK Refinement Module Walkthrough")
print("=" * 70)

# Build the config that Conversation.run() will also use later.
iterative_config = IterativeRefinementConfig(
    success_threshold=SUCCESS_THRESHOLD,
    issue_threshold=ISSUE_THRESHOLD,
    max_iterations=MAX_ITERATIONS,
)
print(
    f"Config: success_threshold={iterative_config.success_threshold}, "
    f"issue_threshold={iterative_config.issue_threshold}, "
    f"max_iterations={iterative_config.max_iterations}"
)

# This demo critic is never sent over the network in Part 1. It only shows the
# local refinement policy and APIBasedCritic-specific prompt generation.
demo_critic = APIBasedCritic(
    server_url="https://example.invalid/vllm",
    api_key="example-api-key",
    model_name="critic",
    iterative_refinement=iterative_config,
)

# --- Scenario A: low score triggers refinement ---
low_result = CriticResult(
    score=0.4,
    message="Agent left several requirements incomplete",
    metadata={
        "categorized_features": {
            "agent_behavioral_issues": [
                {
                    "name": "incomplete_implementation",
                    "display_name": "Incomplete Implementation",
                    "probability": 0.9,
                },
                {
                    "name": "missing_tests",
                    "display_name": "Missing Unit Tests",
                    "probability": 0.8,
                },
            ]
        }
    },
)

decision_a = demo_critic.evaluate_refinement(low_result)
print(f"\nScenario A — score {low_result.score}:")
print(f"  should_refine = {decision_a.should_refine}")
print(f"  triggered_issues ({len(decision_a.triggered_issues)}):")
for issue in decision_a.triggered_issues:
    name = issue.get("display_name", issue.get("name"))
    prob = issue.get("probability", 0)
    print(f"    - {name} ({prob:.0%})")

prompt_a = demo_critic.get_followup_prompt(low_result, iteration=1)
print(f"  follow-up prompt:\n{prompt_a}")

# --- Scenario B: high score but high-probability issue still refines ---
issue_result = CriticResult(
    score=0.85,
    message="Looks good overall but tests are flaky",
    metadata={
        "categorized_features": {
            "agent_behavioral_issues": [
                {
                    "name": "flaky_tests",
                    "display_name": "Flaky Test Suite",
                    "probability": 0.82,
                }
            ]
        }
    },
)

generic_decision_b = iterative_config.evaluate(issue_result)
decision_b = demo_critic.evaluate_refinement(issue_result)
print(f"Scenario B — score {issue_result.score} (above threshold):")
print(f"  generic config should_refine = {generic_decision_b.should_refine}")
print(
    f"  APIBasedCritic should_refine = {decision_b.should_refine}  "
    f"(agent issue >= {ISSUE_THRESHOLD:.0%} overrides)"
)
print(f"  triggered_issues: {[i['display_name'] for i in decision_b.triggered_issues]}")

# --- Scenario C: clean pass ---
good_result = CriticResult(score=0.92, message="All requirements met")
decision_c = demo_critic.evaluate_refinement(good_result)
print(f"\nScenario C — score {good_result.score}:")
print(f"  should_refine = {decision_c.should_refine}")

print("\n✅ Part 1 complete — refinement module works.\n")


# ===================================================================
# Part 2: End-to-end with APIBasedCritic + Conversation.run()
# ===================================================================
# This section requires a critic server and a valid LLM API key.
# Set SKIP_E2E=1 to run only Part 1.
if os.getenv("SKIP_E2E", "").strip() in ("1", "true", "yes"):
    print("SKIP_E2E is set — skipping Part 2.")
    print("\nEXAMPLE_COST: 0")
    raise SystemExit(0)

print("=" * 70)
print("Part 2 — End-to-end with APIBasedCritic")
print("=" * 70)

TASK_PROMPT = """\
Create a Python word statistics tool called `wordstats` that analyzes text files.

## Structure

Create directory `wordstats/` with:
- `stats.py` - Main module with `analyze_file(filepath)` function
- `cli.py` - Command-line interface
- `tests/test_stats.py` - Unit tests

## Requirements for stats.py

The `analyze_file(filepath)` function must return a dict with these EXACT keys:
- `lines`: total line count (including empty lines)
- `words`: word count
- `chars`: character count (including whitespace)
- `unique_words`: count of unique words (case-insensitive)

### Important edge cases:
1. Empty files must return all zeros, not raise an exception
2. Hyphenated words count as ONE word (e.g., "well-known" = 1 word)
3. Numbers like "123" or "3.14" are NOT counted as words
4. Contractions like "don't" count as ONE word
5. File not found must raise FileNotFoundError with a clear message

## Requirements for cli.py

When run as `python cli.py <filepath>`:
- Print each stat on its own line: "Lines: X", "Words: X", etc.
- Exit with code 1 if file not found, printing error to stderr
- Exit with code 0 on success

## Required Tests (test_stats.py)

Write tests that verify:
1. Basic counting on normal text
2. Empty file returns all zeros
3. Hyphenated words counted correctly
4. Numbers are excluded from word count
5. FileNotFoundError raised for missing files

## Verification Steps

1. Create a sample file `sample.txt` with this EXACT content (no trailing newline):
```
Hello world!
This is a well-known test file.

It has 5 lines, including empty ones.
Numbers like 42 and 3.14 don't count as words.
```

2. Run: `python wordstats/cli.py sample.txt`
3. Run the tests: `python -m pytest wordstats/tests/ -v`
   ALL tests must pass.

The task is complete ONLY when all files exist and all tests pass.
"""

llm_api_key = get_required_env("LLM_API_KEY")
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
llm = LLM(
    model=llm_model,
    api_key=llm_api_key,
    top_p=0.95,
    base_url=os.getenv("LLM_BASE_URL"),
)

workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
print(f"📁 Created workspace: {workspace}")

# Auto-configure for All-Hands proxy or use explicit env vars
critic = get_default_critic(llm)
if critic is None:
    print("⚠️  No All-Hands LLM proxy detected, trying explicit env vars...")
    critic = APIBasedCritic(
        server_url=get_required_env("CRITIC_SERVER_URL"),
        api_key=get_required_env("CRITIC_API_KEY"),
        model_name=get_required_env("CRITIC_MODEL_NAME"),
        iterative_refinement=iterative_config,
    )
else:
    critic = critic.model_copy(update={"iterative_refinement": iterative_config})

agent = Agent(
    llm=llm,
    tools=[
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ],
    critic=critic,
)

conversation = Conversation(agent=agent, workspace=str(workspace))

print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
print(f"Issue threshold:   {ISSUE_THRESHOLD:.0%}")
print(f"Max iterations:    {MAX_ITERATIONS}")

# Conversation.run() delegates refinement decisions and follow-up prompts to the
# configured critic internally, so no hand-written loop is needed.
conversation.send_message(TASK_PROMPT)
conversation.run()

print("\nCreated files:")
for path in sorted(workspace.rglob("*")):
    if path.is_file():
        relative = path.relative_to(workspace)
        print(f"  - {relative}")

cost = llm.metrics.accumulated_cost
print(f"\nEXAMPLE_COST: {cost:.4f}")
