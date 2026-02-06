"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates how to use a critic model to shepherd an agent through
complex, multi-step tasks. The critic evaluates the agent's progress and provides
feedback that can trigger follow-up prompts when the agent hasn't completed the
task successfully.

Key concepts demonstrated:
1. Setting up a critic to evaluate agent actions in real-time
2. Capturing critic results via callbacks
3. Using low critic scores to trigger corrective follow-up prompts
4. Iterating until the task is completed successfully or max iterations reached

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-configured
using the same base_url with /vllm suffix and "critic" as the model name.
"""

import os
import re
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Event, Tool
from openhands.sdk.critic import APIBasedCritic, CriticResult
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


# Configuration
# Higher threshold (70%) makes it more likely the agent needs multiple iterations
# to demonstrate the value of the critic model for iterative refinement
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "0.70"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


def get_default_critic(llm: LLM) -> CriticBase | None:
    """Auto-configure critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    Returns None if base_url doesn't match or api_key is not set.
    """
    base_url = llm.base_url
    api_key = llm.api_key
    if base_url is None or api_key is None:
        return None

    # Match: llm-proxy.{env}.all-hands.dev (e.g., staging, prod, eval)
    pattern = r"^https?://llm-proxy\.[^./]+\.all-hands\.dev"
    if not re.match(pattern, base_url):
        return None

    return APIBasedCritic(
        server_url=f"{base_url.rstrip('/')}/vllm",
        api_key=api_key,
        model_name="critic",
    )


class CriticResultCollector:
    """Collects critic results from conversation events via callback."""

    def __init__(self) -> None:
        self.results: list[CriticResult] = []
        self.latest_result: CriticResult | None = None

    def callback(self, event: Event) -> None:
        """Callback to capture critic results from events."""
        if isinstance(event, (ActionEvent, MessageEvent)):
            if event.critic_result is not None:
                self.results.append(event.critic_result)
                self.latest_result = event.critic_result
                print(f"\n📊 Critic Score: {event.critic_result.score:.3f}")
                if event.critic_result.message:
                    print(f"   Details: {event.critic_result.message[:100]}...")

    def reset(self) -> None:
        """Reset collected results for a new iteration."""
        self.results = []
        self.latest_result = None


# Task prompt designed to be moderately complex with subtle requirements.
# The task is simple enough to complete in 1-2 iterations, but has specific
# requirements that are easy to miss - triggering critic feedback.
INITIAL_TASK_PROMPT = """\
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

### Important edge cases (often missed!):
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
   Expected output:
   - Lines: 5
   - Words: 21
   - Chars: 130
   - Unique words: 21

3. Run the tests: `python -m pytest wordstats/tests/ -v`
   ALL tests must pass.

The task is complete ONLY when:
- All files exist
- The CLI outputs the correct stats for sample.txt
- All 5+ tests pass
"""


# Follow-up prompt template for iterative refinement.
# Uses the critic's evaluation to craft specific guidance for improvement.
FOLLOWUP_PROMPT_TEMPLATE = """\
The task appears incomplete (iteration {iteration}, \
success likelihood: {score_percent:.1f}%).
{issues_text}

Please review what you've done and verify each requirement:

## File Checklist
- [ ] wordstats/stats.py - analyze_file() function
- [ ] wordstats/cli.py - Command-line interface
- [ ] wordstats/tests/test_stats.py - Unit tests (5+ tests)
- [ ] sample.txt - Test file with exact content specified

## Edge Cases Often Missed
- [ ] Empty files return {{"lines": 0, "words": 0, "chars": 0, "unique_words": 0}}
- [ ] Hyphenated words like "well-known" count as 1 word
- [ ] Numbers (42, 3.14) are NOT counted as words
- [ ] Contractions like "don't" count as 1 word
- [ ] FileNotFoundError raised with clear message for missing files

## Expected Output for sample.txt
Run: python wordstats/cli.py sample.txt
- Lines: 5
- Words: 21 (numbers excluded, hyphenated words count as one)
- Chars: 130
- Unique words: 21

## Verification Steps
1. Check if all files exist: `ls -la wordstats/ wordstats/tests/`
2. Run the CLI: `python wordstats/cli.py sample.txt`
3. Verify output matches expected values exactly
4. Run tests: `python -m pytest wordstats/tests/ -v`

Common mistakes to fix:
- Counting numbers as words (they shouldn't be)
- Splitting hyphenated words (they should stay together)
- Wrong character count (remember newlines!)

List what's working and what needs fixing, then complete the task.
"""


def get_followup_prompt(critic_result: CriticResult, iteration: int) -> str:
    """Generate a follow-up prompt based on critic feedback."""
    score_percent = critic_result.score * 100

    # Extract potential issues from critic metadata if available
    issues = []
    if critic_result.metadata and "categorized_features" in critic_result.metadata:
        categorized = critic_result.metadata["categorized_features"]
        if "agent_behavioral_issues" in categorized:
            issues = [
                f.get("display_name", f.get("name", "Unknown issue"))
                for f in categorized["agent_behavioral_issues"]
            ]

    issues_text = ""
    if issues:
        issues_text = f"\nPotential issues identified: {', '.join(issues)}"

    return FOLLOWUP_PROMPT_TEMPLATE.format(
        iteration=iteration,
        score_percent=score_percent,
        issues_text=issues_text,
    )


def main() -> None:
    """Run the iterative refinement example with critic model."""
    llm_api_key = get_required_env("LLM_API_KEY")
    llm = LLM(
        # model="anthropic/claude-haiku-4-5",
        model="litellm_proxy/moonshot/kimi-k2.5",
        api_key=llm_api_key,
        top_p=0.95,
        base_url=os.getenv("LLM_BASE_URL", None),
    )

    # Setup critic
    critic = get_default_critic(llm)
    if critic is None:
        print("⚠️  No All-Hands LLM proxy detected, trying explicit env vars...")
        critic = APIBasedCritic(
            server_url=get_required_env("CRITIC_SERVER_URL"),
            api_key=get_required_env("CRITIC_API_KEY"),
            model_name=get_required_env("CRITIC_MODEL_NAME"),
        )

    # Create agent with critic
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
        critic=critic,
    )

    # Create workspace and collector
    workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
    print(f"📁 Created workspace: {workspace}")
    collector = CriticResultCollector()

    # Create conversation with callback
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        callbacks=[collector.callback],
    )

    print("\n" + "=" * 70)
    print("🚀 Starting Iterative Refinement with Critic Model")
    print("=" * 70)
    print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
    print(f"Max iterations: {MAX_ITERATIONS}")

    # Initial task
    print("\n--- Iteration 1: Initial Task ---")
    conversation.send_message(INITIAL_TASK_PROMPT)
    conversation.run()

    iteration = 1
    while iteration < MAX_ITERATIONS:
        # Check critic result
        if collector.latest_result is None:
            print("\n⚠️  No critic result available, assuming task incomplete")
            score = 0.0
        else:
            score = collector.latest_result.score

        print(f"\n📈 Iteration {iteration} final score: {score:.3f}")

        if score >= SUCCESS_THRESHOLD:
            print(f"✅ Success threshold ({SUCCESS_THRESHOLD:.0%}) met!")
            break

        # Prepare for next iteration - save latest_result BEFORE reset
        iteration += 1
        last_result = collector.latest_result or CriticResult(score=0.0, message=None)
        collector.reset()

        print(f"\n--- Iteration {iteration}: Follow-up Refinement ---")
        print(f"Score {score:.3f} < threshold {SUCCESS_THRESHOLD:.3f}, sending...")

        followup_prompt = get_followup_prompt(last_result, iteration)
        conversation.send_message(followup_prompt)
        conversation.run()

    # Final summary
    print("\n" + "=" * 70)
    print("📊 ITERATIVE REFINEMENT COMPLETE")
    print("=" * 70)
    print(f"Total iterations: {iteration}")

    if collector.latest_result:
        final_score = collector.latest_result.score
        print(f"Final critic score: {final_score:.3f}")
        print(f"Success: {'✅ YES' if final_score >= SUCCESS_THRESHOLD else '❌ NO'}")
    else:
        print("Final critic score: N/A (no critic results)")

    # List created files
    print("\nCreated files:")
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            relative = path.relative_to(workspace)
            print(f"  - {relative}")

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"\nEXAMPLE_COST: {cost:.4f}")


if __name__ == "__main__":
    main()
