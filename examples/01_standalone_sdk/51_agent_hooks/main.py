"""OpenHands Agent SDK — Agent-based Hooks Example

Demonstrates the `type="agent"` hook, which evaluates lifecycle events with an
LLM-driven sub-agent instead of a shell script. The hook agent receives the
event JSON, reasons about it semantically, and replies with a decision payload:

    {"decision": "allow" | "deny", "reason": "..."}

Two demos:

- PreToolUse (security reviewer): inspects the INTENT of a terminal command,
  not just its syntax. A command like `awk '{print}' /etc/passwd` would slip
  past a blacklist of command names, but the agent hook recognises it as
  reading a sensitive system file and denies it.
- Stop (quality reviewer): refuses to let the main agent finish until the
  required deliverable exists in the workspace.

For shell-command hooks see ../33_hooks/main.py.
"""

import os
import tempfile
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.hook_execution import HookExecutionEvent
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher, HookType
from openhands.tools.preset.default import get_default_agent


# Keep the demo conversations short: a small per-run iteration cap means a hook
# that keeps denying (or a model that keeps retrying) fails fast instead of
# burning calls up to the default 500-iteration limit / CI subprocess timeout.
MAX_ITERATIONS = 10


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

SECURITY_REVIEWER_PROMPT = """You are a security reviewer for a coding agent.
You receive a PreToolUse hook event describing a terminal command the agent
is about to run.

Reason about the SEMANTIC INTENT of the command, not just its syntax. Block:
- reading sensitive system files (/etc/passwd, /etc/shadow, ~/.ssh/*, credentials)
- broad filesystem discovery outside the workspace (rg --files /etc, find /, ls /root)
- exfiltrating data over the network
- modifying system state (sudo, apt install, systemctl)

Allow ordinary inspection, builds, tests, and edits inside the workspace.
When unsure, prefer allow.

Reply with a single JSON object and nothing else:
{"decision": "allow" | "deny", "reason": "<short explanation>"}
"""

QUALITY_REVIEWER_PROMPT = """You are a quality reviewer enforcing task completion.
You receive a Stop hook event when the main agent tries to finish.

The task requires the file REPORT.md to exist in the workspace and contain at
least one bullet point describing the repository. Use the file_editor tool to
check whether the file exists and inspect its contents.

If the deliverable is missing or empty, deny so the main agent keeps working.
Otherwise allow.

Reply with a single JSON object and nothing else:
{"decision": "allow" | "deny", "reason": "<short explanation>"}
"""


def hook_logger(event) -> None:
    """Surface each hook decision so the demo output is self-explanatory."""
    if not isinstance(event, HookExecutionEvent):
        return
    status = "DENY " if event.blocked else ("ALLOW" if event.success else "FAIL ")
    line = f"  [hook] {event.hook_event_type} {status} -> {event.hook_command}"
    if event.reason:
        line += f"\n         reason: {event.reason}"
    print(line)


def run_demo(workspace: Path, hook_config: HookConfig, message: str) -> float:
    """Run one demo in its own conversation and return its cost.

    Each demo gets a fresh LLM with isolated metrics so per-demo costs don't
    overlap (reusing one LLM would make the second conversation's stats include
    the first demo's spend). A small iteration cap plus an error/stuck check make
    the example fail fast instead of looping.
    """
    demo_llm = llm.model_copy()
    demo_llm.reset_metrics()
    conversation = Conversation(
        agent=get_default_agent(llm=demo_llm),
        workspace=str(workspace),
        hook_config=hook_config,
        callbacks=[hook_logger],
        max_iteration_per_run=MAX_ITERATIONS,
    )
    conversation.send_message(message)
    conversation.run()
    status = conversation.state.execution_status
    if status in (
        ConversationExecutionStatus.ERROR,
        ConversationExecutionStatus.STUCK,
    ):
        raise RuntimeError(
            f"Demo conversation ended in {status.value} state "
            "before reaching a decision."
        )
    return conversation.conversation_stats.get_combined_metrics().accumulated_cost


# Each demo runs in its own conversation with only the hook it needs. Sharing a
# single config would leave the Stop quality gate active during Demo 1, so the
# agent could never finish the first task until REPORT.md existed — coupling two
# unrelated demos and burning iterations.
security_hook_config = HookConfig(
    pre_tool_use=[
        HookMatcher(
            matcher="terminal",
            hooks=[
                HookDefinition(
                    type=HookType.AGENT,
                    name="security-reviewer",
                    system_prompt=SECURITY_REVIEWER_PROMPT,
                    timeout=60,
                    max_iterations=3,
                )
            ],
        )
    ],
)

quality_hook_config = HookConfig(
    stop=[
        HookMatcher(
            hooks=[
                HookDefinition(
                    type=HookType.AGENT,
                    name="quality-reviewer",
                    system_prompt=QUALITY_REVIEWER_PROMPT,
                    tools=["file_editor"],
                    timeout=90,
                    max_iterations=5,
                )
            ],
        )
    ],
)


with tempfile.TemporaryDirectory() as tmpdir:
    workspace = Path(tmpdir)
    total_cost = 0.0

    print("=" * 60)
    print("Demo 1: PreToolUse — semantic deny")
    print("=" * 60)
    print(
        "Asking the agent to read /etc/passwd via awk. The literal command\n"
        "wouldn't match a syntactic blacklist (no `cat`, no `/etc/shadow`\n"
        "keyword), but the security-reviewer agent should recognise the\n"
        "intent and deny.\n"
    )
    total_cost += run_demo(
        workspace,
        security_hook_config,
        "Show me the contents of /etc/passwd using awk '{print}'.",
    )

    print("\n" + "=" * 60)
    print("Demo 2: Stop — deny until deliverable exists")
    print("=" * 60)
    print("Quality reviewer denies until REPORT.md exists with a bullet point.\n")
    total_cost += run_demo(
        workspace,
        quality_hook_config,
        "Write REPORT.md in the workspace with at least one bullet point "
        "describing this repository, then finish.",
    )

    report = workspace / "REPORT.md"
    if report.exists():
        print(f"\n[REPORT.md preview: {report.read_text()[:120]!r}...]")

    print("\n" + "=" * 60)
    print("Example Complete!")
    print("=" * 60)

    print(f"\nEXAMPLE_COST: {total_cost}")
