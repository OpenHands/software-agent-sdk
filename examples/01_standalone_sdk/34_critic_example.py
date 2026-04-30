"""Critic with SDK Refinement Module Example.

This is EXPERIMENTAL.

This example demonstrates how to use the SDK's built-in critic refinement module
to drive iterative agent improvement. Instead of hand-writing retry loops and
follow-up prompt generation, the SDK handles everything via:

1. **IterativeRefinementConfig** — declares success/issue thresholds and max
   iterations; its `evaluate()` and `build_followup_prompt()` methods delegate
   to the `openhands.sdk.critic.refinement` module.
2. **Conversation.run()** — automatically retries when the config says
   refinement is needed.
3. **Standalone refinement helpers** — `evaluate_iterative_refinement()` and
   `build_refinement_message()` can be used outside a Conversation for custom
   workflows.

Critic selection:
- When running behind the All-Hands LLM proxy, an ``APIBasedCritic`` is
  auto-configured.
- Explicit ``CRITIC_SERVER_URL`` / ``CRITIC_API_KEY`` / ``CRITIC_MODEL_NAME``
  env vars also produce an ``APIBasedCritic``.
- Otherwise, the example falls back to a lightweight ``FileCheckCritic`` that
  checks file existence and runs tests locally, so you can try the refinement
  flow without a critic server.
"""

import os
import re
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import (
    APIBasedCritic,
    CriticResult,
    IterativeRefinementConfig,
)
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.critic.refinement import (
    build_refinement_message,
    evaluate_iterative_refinement,
)
from openhands.sdk.event import LLMConvertibleEvent
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


# ---------------------------------------------------------------------------
# FileCheckCritic: lightweight local fallback (no server needed)
# ---------------------------------------------------------------------------
class FileCheckCritic(CriticBase):
    """Critic that checks whether expected files exist and tests pass.

    Populates ``categorized_features`` so the SDK refinement module's
    issue-threshold logic is exercised end-to-end.
    """

    workspace: str

    def evaluate(
        self,
        events: Sequence[LLMConvertibleEvent],  # noqa: ARG002
        git_patch: str | None = None,  # noqa: ARG002
    ) -> CriticResult:
        ws = Path(self.workspace)
        required_files = [
            "wordstats/stats.py",
            "wordstats/cli.py",
            "wordstats/tests/test_stats.py",
            "sample.txt",
        ]
        missing = [f for f in required_files if not (ws / f).exists()]

        tests_pass = False
        test_file = ws / "wordstats/tests/test_stats.py"
        if test_file.exists():
            result = subprocess.run(
                ["python", "-m", "pytest", str(test_file), "-q"],
                capture_output=True,
                text=True,
                cwd=str(ws),
                timeout=30,
            )
            tests_pass = result.returncode == 0

        file_score = (len(required_files) - len(missing)) / len(required_files)
        test_score = 1.0 if tests_pass else 0.0
        score = 0.6 * file_score + 0.4 * test_score

        issues: list[dict[str, Any]] = []
        if missing:
            issues.append(
                {
                    "name": "missing_files",
                    "display_name": "Missing Required Files",
                    "probability": min(len(missing) / len(required_files) + 0.5, 1.0),
                }
            )
        if not tests_pass and test_file.exists():
            issues.append(
                {
                    "name": "failing_tests",
                    "display_name": "Unit Tests Failing",
                    "probability": 0.85,
                }
            )

        metadata = {"categorized_features": {"agent_behavioral_issues": issues}}
        test_label = "pass" if tests_pass else "fail"
        return CriticResult(
            score=score,
            message=(f"Files: {file_score:.0%}, Tests: {test_label}"),
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# Task prompt
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
llm_api_key = get_required_env("LLM_API_KEY")
llm_model = os.getenv("LLM_MODEL", "anthropic/claude-haiku-4-5-20251001")
llm = LLM(
    model=llm_model,
    api_key=llm_api_key,
    top_p=0.95,
    base_url=os.getenv("LLM_BASE_URL"),
)

# Create workspace early so the local critic fallback can inspect it
workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
print(f"📁 Created workspace: {workspace}")

# --- SDK refinement module: IterativeRefinementConfig ---
# The config declares thresholds and max iterations.  Its evaluate() and
# build_followup_prompt() methods delegate to the refinement module's
# evaluate_iterative_refinement() and build_refinement_message() helpers.
iterative_config = IterativeRefinementConfig(
    success_threshold=SUCCESS_THRESHOLD,
    issue_threshold=ISSUE_THRESHOLD,
    max_iterations=MAX_ITERATIONS,
)

# --- Critic selection ---
# 1. All-Hands LLM proxy → APIBasedCritic (auto-configured)
# 2. Explicit CRITIC_SERVER_URL env var → APIBasedCritic
# 3. Otherwise → FileCheckCritic (local, no server needed)
critic: CriticBase
api_critic = get_default_critic(llm)
if api_critic is not None:
    critic = api_critic.model_copy(update={"iterative_refinement": iterative_config})
    print("🔌 Using APIBasedCritic (All-Hands LLM proxy)")
elif os.getenv("CRITIC_SERVER_URL"):
    critic = APIBasedCritic(
        server_url=get_required_env("CRITIC_SERVER_URL"),
        api_key=get_required_env("CRITIC_API_KEY"),
        model_name=get_required_env("CRITIC_MODEL_NAME"),
        iterative_refinement=iterative_config,
    )
    print("🔌 Using APIBasedCritic (explicit env vars)")
else:
    critic = FileCheckCritic(
        workspace=str(workspace),
        iterative_refinement=iterative_config,
    )
    print("📋 Using FileCheckCritic (local fallback)")

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

print("\n" + "=" * 70)
print("🚀 Iterative Refinement via SDK Critic Refinement Module")
print("=" * 70)
print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
print(f"Issue threshold:   {ISSUE_THRESHOLD:.0%}")
print(f"Max iterations:    {MAX_ITERATIONS}")

# Conversation.run() now delegates to IterativeRefinementConfig.evaluate()
# and build_followup_prompt() — no hand-written retry loop needed.
conversation.send_message(TASK_PROMPT)
conversation.run()

# ---------------------------------------------------------------------------
# Demonstrate standalone refinement helpers (outside Conversation)
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("📊 Standalone Refinement Helpers Demo")
print("=" * 70)

# Run the critic one more time to get a final result for demonstration

final_result = critic.evaluate(events=[], git_patch=None)
print(f"Final critic score: {final_result.score:.2f}")
print(f"Final message:      {final_result.message}")

# Use standalone evaluate_iterative_refinement()
decision = evaluate_iterative_refinement(
    final_result,
    success_threshold=SUCCESS_THRESHOLD,
    issue_threshold=ISSUE_THRESHOLD,
)
print(f"\nShould refine?      {decision.should_refine}")
if decision.triggered_issues:
    print("Triggered issues:")
    for issue in decision.triggered_issues:
        name = issue.get("display_name", issue.get("name"))
        prob = issue.get("probability", 0)
        print(f"  - {name} ({prob:.0%})")

# Use standalone build_refinement_message()
if decision.should_refine:
    prompt = build_refinement_message(
        final_result,
        iteration=1,
        max_iterations=MAX_ITERATIONS,
        issue_threshold=ISSUE_THRESHOLD,
        triggered_issues=decision.triggered_issues,
    )
    print(f"\nGenerated follow-up prompt:\n{prompt}")
else:
    print("\n✅ Task meets quality threshold — no refinement needed.")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("Created files:")
for path in sorted(workspace.rglob("*")):
    if path.is_file():
        relative = path.relative_to(workspace)
        print(f"  - {relative}")

cost = llm.metrics.accumulated_cost
print(f"\nEXAMPLE_COST: {cost:.4f}")
