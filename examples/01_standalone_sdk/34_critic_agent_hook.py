"""Critic Agent with Callback Hook Example

This example demonstrates the critic agent feature with Python callback hooks:

- A Stop hook blocks the agent from finishing until a critic approves the
  current git diff.
- The critic (`AgentReviewCritic`) spawns a *separate* OpenHands agent to do a
  PR-style review of the diff.
- The hook uses a Python callback function instead of a shell script.

How it works:
1) We create a temporary git repo in a temp workspace.
2) We ask the agent to make a change.
3) The Stop hook runs via a Python callback, invokes the critic, and
   denies stopping if the critic says `not_pass`, feeding the critic
   summary back to the agent.
4) The agent can then fix the issues and try again.

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
from openhands.sdk.critic.impl.agent_review import (
    AgentReviewCritic,
    create_critic_stop_hook,
)
from openhands.sdk.hooks import HookConfig, HookMatcher
from openhands.tools.preset.critic import get_critic_agent
from openhands.tools.preset.default import get_default_agent


signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)


def _git_patch(workspace: Path) -> str:
    return subprocess.check_output(["git", "diff"], cwd=workspace, text=True)


def main() -> None:
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

        # Create the critic with the same LLM and a custom agent factory
        critic = AgentReviewCritic(
            llm=llm,
            agent_factory=get_critic_agent,
            review_style="roasted",
        )

        # Create a callback-based stop hook using the helper function
        hook_config = HookConfig(
            stop=[HookMatcher(hooks=[create_critic_stop_hook(critic, str(workspace))])]
        )

        agent = get_default_agent(llm=llm)
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace),
            hook_config=hook_config,
        )

        print("=" * 80)
        print("Step 1: Ask agent to add a new function")
        print("=" * 80)
        print(
            "\nThe agent will try to finish, but the critic hook will review the diff."
        )
        print(
            "If the critic finds issues, it will deny stopping and provide feedback.\n"
        )

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


if __name__ == "__main__":
    main()
