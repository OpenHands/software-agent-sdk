"""Critic Agent with Iterative Refinement Example

This example demonstrates two approaches for using the AgentReviewCritic:

1. **Iterative Refinement (Recommended)**: Uses the built-in iterative refinement
   mechanism where the critic evaluates the agent's work and automatically triggers
   follow-up prompts if the quality threshold isn't met.

2. **Stop Hook (Alternative)**: Uses a callback hook to block the agent from
   finishing until the critic approves the current git diff.

The AgentReviewCritic spawns a *separate* OpenHands agent to do a PR-style
review of the git diff.

How Iterative Refinement works:
1) We create a temporary git repo in a temp workspace.
2) We configure the critic with IterativeRefinementConfig.
3) The agent makes changes and tries to finish.
4) The critic evaluates the git diff and provides a score.
5) If the score is below the threshold, a follow-up prompt is sent automatically.
6) The process repeats until the threshold is met or max iterations reached.

Requirements:
- export LLM_API_KEY=...
- optional: LLM_MODEL, LLM_BASE_URL

Run:
  python examples/01_standalone_sdk/34_critic_agent_hook.py

"""

import os
import signal
import subprocess
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.critic import IterativeRefinementConfig
from openhands.sdk.critic.impl.agent_review import AgentReviewCritic
from openhands.tools.preset.critic import get_critic_agent
from openhands.tools.preset.default import get_default_agent


signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


# Configuration for iterative refinement
# The critic will evaluate the git diff and trigger follow-up prompts
# if the score is below the threshold
SUCCESS_THRESHOLD = float(os.getenv("CRITIC_SUCCESS_THRESHOLD", "1.0"))
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


def _git_patch(workspace: Path) -> str:
    return subprocess.check_output(["git", "diff"], cwd=workspace, text=True)


api_key = os.getenv("LLM_API_KEY")
assert api_key, "LLM_API_KEY environment variable is not set"

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

with tempfile.TemporaryDirectory() as tmpdir:
    workspace = Path(tmpdir)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "example@example.com")
    _git(workspace, "config", "user.name", "Example")

    # Create initial file
    (workspace / "calc.py").write_text(
        """def add(a, b):
    return a + b


if __name__ == "__main__":
    print(add(1, 2))
"""
    )
    _git(workspace, "add", "calc.py")
    _git(workspace, "commit", "-m", "init", "-q")

    # Create the critic with iterative refinement config
    # The critic will spawn a separate agent to review the git diff
    iterative_config = IterativeRefinementConfig(
        success_threshold=SUCCESS_THRESHOLD,
        max_iterations=MAX_ITERATIONS,
    )

    critic = AgentReviewCritic(
        llm=llm,
        agent_factory=get_critic_agent,
        review_style="roasted",
        workspace_dir=str(workspace),  # Tell critic where to get git diff
        iterative_refinement=iterative_config,
    )

    # Create the main agent with the critic attached
    # We start with the default agent and add the critic
    base_agent = get_default_agent(llm=llm)
    agent = base_agent.model_copy(update={"critic": critic})

    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
    )

    print("=" * 80)
    print("🚀 Iterative Refinement with Code Review Critic")
    print("=" * 80)
    print(f"\nSuccess threshold: {SUCCESS_THRESHOLD:.0%}")
    print(f"Max iterations: {MAX_ITERATIONS}")
    print("\nThe agent will make changes, and the critic will review the git diff.")
    print("If the critic finds issues, it will provide feedback for improvement.\n")

    conversation.send_message(
        "Edit calc.py to add a new function multiply(a, b) that "
        "multiplies two numbers. Add proper type hints and a docstring. "
        "Then finish."
    )
    conversation.run()

    # Show the current diff
    patch = _git_patch(workspace)
    if patch:
        print("\n[Current git diff]")
        print(patch[:500] + "..." if len(patch) > 500 else patch)

    print("\n" + "=" * 80)
    print("Example Complete!")
    print("=" * 80)

    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")
