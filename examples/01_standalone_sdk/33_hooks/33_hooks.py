"""OpenHands Agent SDK — Hooks Example

Demonstrates the OpenHands hooks system.
Hooks are shell scripts that run at key lifecycle events:

- PreToolUse: Block dangerous commands before execution
- PostToolUse: Log tool usage after execution
- UserPromptSubmit: Inject context into user messages
- Stop: Enforce task completion criteria

The hook scripts are in the scripts/ directory alongside this file.
"""

import os
import signal
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.tools.preset.default import get_default_agent


signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

SCRIPT_DIR = Path(__file__).parent / "hook_scripts"

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

# Create temporary workspace with git repo
with tempfile.TemporaryDirectory() as tmpdir:
    workspace = Path(tmpdir)
    os.system(f"cd {workspace} && git init -q && echo 'test' > file.txt")

    log_file = workspace / "tool_usage.log"
    summary_file = workspace / "summary.txt"

    # Configure hooks using the typed approach (recommended)
    # This provides better type safety and IDE support
    hook_config = HookConfig(
        pre_tool_use=[
            HookMatcher(
                matcher="terminal",
                hooks=[
                    HookDefinition(
                        command=str(SCRIPT_DIR / "block_dangerous.sh"),
                        timeout=10,
                    )
                ],
            )
        ],
        post_tool_use=[
            HookMatcher(
                matcher="*",
                hooks=[
                    HookDefinition(
                        command=(f"LOG_FILE={log_file} {SCRIPT_DIR / 'log_tools.sh'}"),
                        timeout=5,
                    )
                ],
            )
        ],
        user_prompt_submit=[
            HookMatcher(
                hooks=[
                    HookDefinition(
                        command=str(SCRIPT_DIR / "inject_git_context.sh"),
                    )
                ],
            )
        ],
        stop=[
            HookMatcher(
                hooks=[
                    HookDefinition(
                        command=(
                            f"SUMMARY_FILE={summary_file} "
                            f"{SCRIPT_DIR / 'require_summary.sh'}"
                        ),
                    )
                ],
            )
        ],
    )

    # Alternative: You can also use .from_dict() for loading from JSON config files
    # Example with a single hook matcher:
    # hook_config = HookConfig.from_dict({
    #     "hooks": {
    #         "PreToolUse": [{
    #             "matcher": "terminal",
    #             "hooks": [{"command": "path/to/script.sh", "timeout": 10}]
    #         }]
    #     }
    # })

    agent = get_default_agent(llm=llm)
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        hook_config=hook_config,
    )

    # Demo 1: Safe command (PostToolUse logs it)
    print("=" * 60)
    print("Demo 1: Safe command - logged by PostToolUse")
    print("=" * 60)
    conversation.send_message("Run: echo 'Hello from hooks!'")
    conversation.run()

    if log_file.exists():
        print(f"\n[Log: {log_file.read_text().strip()}]")

    # Demo 2: Dangerous command (PreToolUse blocks it)
    print("\n" + "=" * 60)
    print("Demo 2: Dangerous command - blocked by PreToolUse")
    print("=" * 60)
    conversation.send_message("Run: rm -rf /tmp/test")
    conversation.run()

    # Demo 3: Context injection + Stop hook enforcement
    print("\n" + "=" * 60)
    print("Demo 3: Context injection + Stop hook")
    print("=" * 60)
    print("UserPromptSubmit injects git status; Stop requires summary.txt\n")
    conversation.send_message(
        "Check what files have changes, then create summary.txt describing the repo."
    )
    conversation.run()

    if summary_file.exists():
        print(f"\n[summary.txt: {summary_file.read_text()[:80]}...]")

    print("\n" + "=" * 60)
    print("Example Complete!")
    print("=" * 60)

    cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")
