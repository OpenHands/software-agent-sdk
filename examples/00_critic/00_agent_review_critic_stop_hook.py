"""AgentReviewCritic + Stop hook example

This example demonstrates the "critic agent" introduced in PR #1706:

- A Stop hook blocks the agent from finishing until a critic approves the
  current git diff.
- The critic (`AgentReviewCritic`) spawns a *separate* OpenHands agent to do a
  PR-style review of the diff.

How it works:
1) We create a temporary git repo in a temp workspace.
2) We ask the agent to make a change that *intentionally* contains a small
   issue (so the critic should fail).
3) The Stop hook runs, invokes the critic, and denies stopping, feeding the
   critic summary back to the agent.
4) We ask the agent to fix the issues and try again.

Requirements:
- export LLM_API_KEY=...
- optional: LLM_MODEL, LLM_BASE_URL

Run:
  python examples/00_critic/00_agent_review_critic_stop_hook.py

"""

import os
import subprocess
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.critic.impl.agent_review import AgentReviewCritic
from openhands.sdk.hooks import HookConfig
from openhands.tools.preset.default import get_default_agent


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

        (workspace / "calc.py").write_text(
            """def add(a, b):
    return a + b


if __name__ == "__main__":
    print(add(1, 2))
"""
        )
        _git(workspace, "add", "calc.py")
        _git(workspace, "commit", "-m", "init", "-q")

        # Create a stop hook script in the workspace. Hooks run as shell commands.
        hook_script = workspace / "critic_stop_hook.py"
        hook_script.write_text(
            """#!/usr/bin/env python3
import json
import os
import subprocess
import sys


def main():
    _ = sys.stdin.read()

    project_dir = os.environ.get("OPENHANDS_PROJECT_DIR", ".")
    patch = subprocess.check_output(["git", "diff"], cwd=project_dir, text=True)

    if not patch.strip():
        print(json.dumps({"decision": "allow"}))
        return 0

    marker = os.path.join(project_dir, ".critic_ok")
    if os.path.exists(marker):
        print(json.dumps({"decision": "allow"}))
        return 0

    print(
        json.dumps(
            {
                "decision": "deny",
                "additionalContext": "Critic has not approved yet.",
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
"""
        )
        hook_script.chmod(0o755)

        hook_config = HookConfig.from_dict(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(hook_script),
                                    "timeout": 30,
                                }
                            ]
                        }
                    ]
                }
            }
        )

        agent = get_default_agent(llm=llm)
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace),
            hook_config=hook_config,
        )

        critic = AgentReviewCritic(llm=llm, review_style="roasted")

        print("=" * 80)
        print(
            "Step 1: Ask agent to introduce a problematic change (critic should fail)"
        )
        print("=" * 80)
        conversation.send_message(
            "Edit calc.py to add a new function multiply(a,b). "
            "Be quick. (Intentionally keep code a bit sloppy.)"
        )
        conversation.run()

        patch1 = _git_patch(workspace)
        result1 = critic.evaluate(events=[], git_patch=patch1)
        print("\n[Critic result #1]\n", result1.model_dump_json(indent=2))

        if result1.success:
            (workspace / ".critic_ok").write_text("ok")
        else:
            # leave marker absent so Stop hook continues to block
            pass

        print("\nTrying to stop (Stop hook should deny if critic failed)...")
        conversation.send_message("Summarize what you did and finish.")
        conversation.run()

        print("\n=" * 80)
        print("Step 2: Ask agent to address critic feedback, then re-run critic")
        print("=" * 80)
        conversation.send_message(
            "Fix calc.py based on the critic feedback above. "
            "Add type hints, formatting, and a minimal __main__ guard."
        )
        conversation.run()

        patch2 = _git_patch(workspace)
        result2 = critic.evaluate(events=[], git_patch=patch2)
        print("\n[Critic result #2]\n", result2.model_dump_json(indent=2))

        if result2.success:
            (workspace / ".critic_ok").write_text("ok")

        print("\nTrying to stop again (Stop hook should allow now)...")
        conversation.send_message("Finish now.")
        conversation.run()

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"\nEXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
