"""OpenHands Agent SDK — Hooks Example

This example demonstrates how to use hooks to intercept and control agent behavior.
Hooks are shell scripts that run at key lifecycle events, enabling:
- Blocking dangerous commands before execution (PreToolUse)
- Logging tool usage after execution (PostToolUse)
- Processing user messages before they reach the agent (UserPromptSubmit)

Hooks are configured in .openhands/hooks.json or passed programmatically.
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


def create_example_hooks(tmpdir: Path) -> tuple[HookConfig, Path]:
    """Create example hook scripts and configuration.

    This creates two hooks:
    1. A PreToolUse hook that blocks 'rm -rf' commands
    2. A PostToolUse hook that logs all tool usage
    """
    # Create a blocking hook that prevents dangerous commands
    # Uses jq for JSON parsing (needed for nested fields like tool_input.command)
    block_script = tmpdir / "block_dangerous.sh"
    block_script.write_text("""#!/bin/bash
# Read JSON input from stdin
input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // ""')

# Block rm -rf commands
if [[ "$command" =~ "rm -rf" ]]; then
    echo '{"decision": "deny", "reason": "rm -rf commands are blocked for safety"}'
    exit 2  # Exit code 2 = block the operation
fi

exit 0  # Exit code 0 = allow the operation
""")
    block_script.chmod(0o755)

    # Create a logging hook that records tool usage
    # Uses OPENHANDS_TOOL_NAME env var (no jq/python needed!)
    log_file = tmpdir / "tool_usage.log"
    log_script = tmpdir / "log_tools.sh"
    log_script.write_text(f"""#!/bin/bash
# OPENHANDS_TOOL_NAME is set by the hooks system
echo "[$(date)] Tool used: $OPENHANDS_TOOL_NAME" >> {log_file}
exit 0
""")
    log_script.chmod(0o755)

    # Create hook configuration
    return HookConfig.from_dict(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "terminal",  # Only match the terminal tool
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(block_script),
                                "timeout": 10,
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "*",  # Match all tools
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(log_script),
                                "timeout": 5,
                            }
                        ],
                    }
                ],
            }
        }
    ), log_file


def main():
    # Configure LLM
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-20250514")
    base_url = os.getenv("LLM_BASE_URL")
    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    # Create a temporary directory for hook scripts
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        # Create example hooks
        hook_config, log_file = create_example_hooks(tmpdir)
        print(f"Created hook scripts in {tmpdir}")

        # Create agent and conversation with hooks
        # Just pass hook_config - it auto-wires everything!
        agent = get_default_agent(llm=llm)
        conversation = Conversation(
            agent=agent,
            workspace=os.getcwd(),
            hook_config=hook_config,
        )

        print("\n" + "=" * 60)
        print("Example 1: Safe command (should work)")
        print("=" * 60)
        conversation.send_message("Please run: echo 'Hello from hooks example!'")
        conversation.run()

        # Show the log file
        if log_file.exists():
            print("\n[Log file contents]")
            print(log_file.read_text())

        print("\n" + "=" * 60)
        print("Example 2: Dangerous command (should be BLOCKED)")
        print("=" * 60)
        conversation.send_message("Please run: rm -rf /tmp/test_delete")
        conversation.run()

        print("\n" + "=" * 60)
        print("Example Complete!")
        print("=" * 60)
        print("\nKey points:")
        print("- PreToolUse hooks run BEFORE tool execution and can block operations")
        print("- PostToolUse hooks run AFTER tool execution for logging/auditing")
        print("- Exit code 2 from a hook blocks the operation")
        print("- Hooks receive JSON on stdin with event details")
        print("- Environment variables like $OPENHANDS_TOOL_NAME simplify simple hooks")
        print("- Hook config can be in .openhands/hooks.json or passed via hook_config")

        # Report cost
        cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
        print(f"EXAMPLE_COST: {cost}")


if __name__ == "__main__":
    main()
