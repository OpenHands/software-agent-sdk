"""OpenHands Agent SDK — Advanced Hooks Example

- UserPromptSubmit hook that injects git context when user mentions code changes
- Stop hook that verifies a task was completed before allowing finish

These patterns are common in production:
- Injecting relevant context (git status, file contents) into user messages
- Enforcing task completion criteria before agent can finish
"""

import os
import signal
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.hooks import HookConfig
from openhands.tools.preset.default import get_default_agent


# Make ^C a clean exit instead of a stack trace
signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))


def create_hooks(workspace: Path) -> tuple[HookConfig, Path]:
    """Create hook scripts for the example.

    Creates:
    1. UserPromptSubmit hook - injects git status when user asks about changes
    2. Stop hook - requires a summary.txt file before allowing completion
    """
    hook_dir = workspace / ".hooks"
    hook_dir.mkdir(exist_ok=True)

    # UserPromptSubmit: Inject git status when user mentions changes/diff/git
    context_script = hook_dir / "inject_git_context.sh"
    context_script.write_text(
        """#!/bin/bash
# Inject git context when user asks about code changes
input=$(cat)

# Check if user is asking about changes, diff, or git
if echo "$input" | grep -qiE "(changes|diff|git|commit|modified)"; then
    # Get git status if in a git repo
    if git rev-parse --git-dir > /dev/null 2>&1; then
        status=$(git status --short 2>/dev/null | head -10)
        if [ -n "$status" ]; then
            # Escape for JSON
            escaped=$(echo "$status" | sed 's/"/\\\\"/g' | tr '\\n' ' ')
            echo "{\\"additionalContext\\": \\"Current git status: $escaped\\"}"
        fi
    fi
fi
exit 0
"""
    )
    context_script.chmod(0o755)

    # Stop hook: Require summary.txt to exist before allowing completion
    summary_file = workspace / "summary.txt"
    stop_script = hook_dir / "require_summary.sh"
    stop_script.write_text(
        f'''#!/bin/bash
# Require a summary.txt file before allowing agent to finish
if [ ! -f "{summary_file}" ]; then
    echo '{{"decision": "deny", "additionalContext": "Create summary.txt first."}}'
    exit 2
fi
exit 0
'''
    )
    stop_script.chmod(0o755)

    config = HookConfig.from_dict(
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [{"type": "command", "command": str(context_script)}],
                    }
                ],
                "Stop": [
                    {
                        "hooks": [{"type": "command", "command": str(stop_script)}],
                    }
                ],
            }
        }
    )

    return config, summary_file


def main():
    # Configure LLM
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Initialize as git repo for the context injection demo
        os.system(f"cd {workspace} && git init -q && echo 'test' > file.txt")

        hook_config, summary_file = create_hooks(workspace)
        print(f"Workspace: {workspace}")
        print(f"Hook scripts created in {workspace}/.hooks/")

        agent = get_default_agent(llm=llm)
        conversation = Conversation(
            agent=agent,
            workspace=str(workspace),
            hook_config=hook_config,
        )

        print("\n" + "=" * 60)
        print("Demo: Context Injection + Task Completion Enforcement")
        print("=" * 60)
        print("\nThe UserPromptSubmit hook will inject git status context.")
        print("The Stop hook requires summary.txt before agent can finish.\n")

        # This message triggers git context injection and task completion
        conversation.send_message(
            "Check what files have changes, then create summary.txt "
            "describing the repo state."
        )
        conversation.run()

        # Verify summary was created
        if summary_file.exists():
            print(f"\n[summary.txt created: {summary_file.read_text()[:100]}...]")
        else:
            print("\n[Warning: summary.txt was not created]")

        print("\n" + "=" * 60)
        print("Example Complete!")
        print("=" * 60)

        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"\nEXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
