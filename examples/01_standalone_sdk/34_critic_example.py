"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates how to use a critic model to shepherd an agent through
complex, multi-step tasks. The critic evaluates the agent's progress and provides
feedback that can trigger follow-up prompts when the agent hasn't completed the
task successfully.

Key concepts demonstrated:
1. Setting up a critic with IterativeRefinementConfig for automatic retry
2. Conversation.run() automatically handles retries based on critic scores
3. Custom follow-up prompt generation via critic.get_followup_prompt()
4. Iterating until the task is completed successfully or max iterations reached

For All-Hands LLM proxy (llm-proxy.*.all-hands.dev), the critic is auto-configured
using the same base_url with /vllm suffix and "critic" as the model name.
"""

import os
import tempfile
from pathlib import Path

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import (
    APIBasedCritic,
    IterativeRefinementConfig,
    get_default_critic,
)
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

    # Setup critic with iterative refinement config
    # The IterativeRefinementConfig tells Conversation.run() to automatically
    # retry the task if the critic score is below the threshold
    iterative_config = IterativeRefinementConfig(
        success_threshold=SUCCESS_THRESHOLD,
        max_iterations=MAX_ITERATIONS,
    )

    # Auto-configure critic for All-Hands proxy or use explicit env vars
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
        # Add iterative refinement config to the auto-configured critic
        critic = critic.model_copy(update={"iterative_refinement": iterative_config})

    # Create agent with critic (iterative refinement is built into the critic)
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
        critic=critic,
    )

    # Create workspace
    workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
    print(f"📁 Created workspace: {workspace}")

    # Create conversation - iterative refinement is handled automatically
    # by Conversation.run() based on the critic's config
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
    )

    print("\n" + "=" * 70)
    print("🚀 Starting Iterative Refinement with Critic Model")
    print("=" * 70)
    print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
    print(f"Max iterations: {MAX_ITERATIONS}")

    # Send the task and run - Conversation.run() handles retries automatically
    conversation.send_message(INITIAL_TASK_PROMPT)
    conversation.run()

    # Print additional info about created files
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
