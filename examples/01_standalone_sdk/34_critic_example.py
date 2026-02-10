"""Iterative Refinement with Critic Model Example.

This is EXPERIMENTAL.

This example demonstrates how to use a critic model to shepherd an agent through
complex, multi-step tasks. The critic evaluates the agent's progress and provides
feedback that can trigger follow-up prompts when the agent hasn't completed the
task successfully.

Two critic modes are supported:

1. **API-based Critic** (CRITIC_MODE=api): Uses an external critic API endpoint.
   Auto-configures for All-Hands LLM proxy, or uses explicit env vars.

2. **Agent Review Critic** (CRITIC_MODE=agent_review): Spawns a separate OpenHands
   agent to do a PR-style review of the git diff.

Key concepts demonstrated:
1. Setting up a critic with IterativeRefinementConfig for automatic retry
2. Conversation.run() automatically handles retries based on critic scores
3. Custom follow-up prompt generation via critic.get_followup_prompt()
4. Iterating until the task is completed successfully or max iterations reached

Requirements:
- export LLM_API_KEY=...
- optional: CRITIC_MODE (api|agent_review), LLM_MODEL, LLM_BASE_URL

Run:
  # API-based critic (default)
  python examples/01_standalone_sdk/34_critic_example.py

  # Agent review critic
  CRITIC_MODE=agent_review python examples/01_standalone_sdk/34_critic_example.py
"""

import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.critic import APIBasedCritic, IterativeRefinementConfig
from openhands.sdk.critic.base import CriticBase
from openhands.sdk.critic.impl.agent_review import AgentReviewCritic
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset.critic import get_critic_agent
from openhands.tools.preset.default import get_default_agent
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


# Configuration
CRITIC_MODE = os.getenv("CRITIC_MODE", "api")  # "api" or "agent_review"
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "0.7"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise ValueError(
        f"Missing required environment variable: {name}. "
        f"Set {name} before running this example."
    )


def get_api_critic(llm: LLM) -> CriticBase | None:
    """Auto-configure API-based critic for All-Hands LLM proxy.

    When the LLM base_url matches `llm-proxy.*.all-hands.dev`, returns an
    APIBasedCritic configured with:
    - server_url: {base_url}/vllm
    - api_key: same as LLM
    - model_name: "critic"

    Returns None if not using All-Hands proxy.
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


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


def _git_patch(workspace: Path) -> str:
    return subprocess.check_output(["git", "diff"], cwd=workspace, text=True)


# Task prompts for different modes
AGENT_REVIEW_TASK = (
    "Edit calc.py to add a new function multiply(a, b) that "
    "multiplies two numbers. Add proper type hints and a docstring. "
    "Then finish."
)

API_CRITIC_TASK = """\
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


# Setup LLM
llm_api_key = get_required_env("LLM_API_KEY")
llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    api_key=SecretStr(llm_api_key),
    top_p=0.95,
    base_url=os.getenv("LLM_BASE_URL", None),
)

# Setup iterative refinement config
iterative_config = IterativeRefinementConfig(
    success_threshold=SUCCESS_THRESHOLD,
    max_iterations=MAX_ITERATIONS,
)

# Create workspace
workspace = Path(tempfile.mkdtemp(prefix="critic_demo_"))
print(f"📁 Created workspace: {workspace}")

# Setup critic based on mode
if CRITIC_MODE == "agent_review":
    # Initialize git repo for agent review mode
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "example@example.com")
    _git(workspace, "config", "user.name", "Example")

    # Create initial file for the task
    (workspace / "calc.py").write_text(
        """def add(a, b):
    return a + b


if __name__ == "__main__":
    print(add(1, 2))
"""
    )
    _git(workspace, "add", "calc.py")
    _git(workspace, "commit", "-m", "init", "-q")

    critic: CriticBase = AgentReviewCritic(
        llm=llm,
        agent_factory=get_critic_agent,
        review_style="roasted",
        workspace_dir=str(workspace),
        iterative_refinement=iterative_config,
    )
    task_prompt = AGENT_REVIEW_TASK
    mode_description = "Agent Review Critic (PR-style code review)"

    # Use default agent preset for agent review mode
    base_agent = get_default_agent(llm=llm)
    agent = base_agent.model_copy(update={"critic": critic})

else:  # API mode
    # Auto-configure critic for All-Hands proxy or use explicit env vars
    api_critic = get_api_critic(llm)
    if api_critic is None:
        print("⚠️  No All-Hands LLM proxy detected, trying explicit env vars...")
        critic = APIBasedCritic(
            server_url=get_required_env("CRITIC_SERVER_URL"),
            api_key=get_required_env("CRITIC_API_KEY"),
            model_name=get_required_env("CRITIC_MODEL_NAME"),
            iterative_refinement=iterative_config,
        )
    else:
        critic = api_critic.model_copy(
            update={"iterative_refinement": iterative_config}
        )
    task_prompt = API_CRITIC_TASK
    mode_description = "API-based Critic"

    # Create agent with tools for API mode
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
        ],
        critic=critic,
    )

# Create conversation
conversation = Conversation(
    agent=agent,
    workspace=str(workspace),
)

print("\n" + "=" * 70)
print(f"🚀 Starting Iterative Refinement with {mode_description}")
print("=" * 70)
print(f"Success threshold: {SUCCESS_THRESHOLD:.0%}")
print(f"Max iterations: {MAX_ITERATIONS}")
print("\nThe agent will work on the task, and the critic will evaluate progress.")
print("If the critic finds issues, it will provide feedback for improvement.\n")

# Send the task and run
conversation.send_message(task_prompt)
conversation.run()

# Show results based on mode
if CRITIC_MODE == "agent_review":
    patch = _git_patch(workspace)
    if patch:
        print("\n[Current git diff]")
        print(patch[:500] + "..." if len(patch) > 500 else patch)
else:
    print("\nCreated files:")
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            relative = path.relative_to(workspace)
            print(f"  - {relative}")

print("\n" + "=" * 70)
print("Example Complete!")
print("=" * 70)

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost:.4f}")
