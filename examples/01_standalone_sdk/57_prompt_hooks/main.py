"""OpenHands Agent SDK - prompt-based hooks example.

Evaluates two synthetic PreToolUse events with one LLM completion each. The
commands are only event data: this example never executes them.
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.hooks import (
    HookConfig,
    HookDefinition,
    HookManager,
    HookMatcher,
    HookType,
)


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
    base_url=os.getenv("LLM_BASE_URL"),
    api_key=SecretStr(api_key),
)

TERMINAL_POLICY = """Evaluate the semantic intent of a terminal command.
Deny commands that recursively delete files, read credentials or sensitive
system files, modify the host system, or exfiltrate data. Allow read-only
workspace inspection, builds, and test commands. When uncertain, deny and give
a concise reason."""

hook_config = HookConfig(
    pre_tool_use=[
        HookMatcher(
            matcher="terminal",
            hooks=[
                HookDefinition(
                    type=HookType.PROMPT,
                    name="terminal-safety",
                    prompt=TERMINAL_POLICY,
                    timeout=30,
                )
            ],
        )
    ]
)

cases = [
    ("python -m pytest -q", True),
    ("find / -type f -delete", False),
]

with tempfile.TemporaryDirectory() as tmpdir:
    stats = ConversationStats()
    manager = HookManager(
        config=hook_config,
        working_dir=str(Path(tmpdir)),
        session_id="prompt-hook-example",
        llm=llm,
        conversation_stats=stats,
    )

    for command, expected_to_continue in cases:
        should_continue, results = manager.run_pre_tool_use(
            tool_name="terminal",
            tool_input={"command": command},
        )
        result = results[0]
        verdict = "ALLOW" if should_continue else "DENY"
        print(f"{verdict:5} {command}")
        print(f"      {result.reason}")
        assert should_continue is expected_to_continue

    cost = stats.get_combined_metrics().accumulated_cost
    print(f"\nEXAMPLE_COST: {cost}")
