"""Live check: Claude Code subagent tool calls carry ``parent_tool_call_id``.

The main agent runs one command itself, then spawns two subagents that each run
one command. Every ACPToolCallEvent is printed with its parent. The subagents'
calls must point at the two Task calls that spawned them; the main agent's own
calls carry no parent.

Prerequisites:
    - Node.js / npx available
    - ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY set (can point to LiteLLM proxy)
    - Optional ACP_MODEL to pick the model (e.g. a LiteLLM model name)

Usage:
    uv run python .pr/acp_subagent_parent_live_check.py
"""

import os
import tempfile

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event import ACPToolCallEvent
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS


PROMPT = (
    "Follow exactly.\n"
    "1. Run the shell command `echo main-agent` yourself, once.\n"
    "2. In ONE message, spawn TWO general-purpose subagents in parallel with "
    "your Task/Agent tool. Subagent A runs `echo subagent-a`; subagent B runs "
    "`echo subagent-b`. Each replies with its command's output.\n"
    "3. When both are back, reply DONE."
)

agent = ACPAgent(
    acp_command=list(ACP_PROVIDERS["claude-code"].default_command),
    acp_model=os.environ.get("ACP_MODEL"),
)

try:
    conversation = Conversation(agent=agent, workspace=tempfile.mkdtemp())
    conversation.send_message(PROMPT)
    conversation.run()
finally:
    agent.close()

calls = [e for e in conversation.state.events if isinstance(e, ACPToolCallEvent)]
print(f"\n{'tool_call_id':<34} {'status':<11} {'parent_tool_call_id':<34} title")
for call in calls:
    print(
        f"{call.tool_call_id:<34} {str(call.status):<11} "
        f"{str(call.parent_tool_call_id):<34} {call.title}"
    )

terminal = {c.tool_call_id: c for c in calls}
main_calls = [c for c in terminal.values() if c.parent_tool_call_id is None]
subagent_calls = [c for c in terminal.values() if c.parent_tool_call_id is not None]
parents = {c.parent_tool_call_id for c in subagent_calls}
own_command = next(c for c in main_calls if "main-agent" in c.title)
print(f"\nsubagent calls: {len(subagent_calls)}, distinct parents: {len(parents)}")
assert len(parents) == 2, "expected the calls of exactly two subagents"
assert parents <= {c.tool_call_id for c in main_calls}, "parent is not a main call"
assert own_command.tool_call_id not in parents, "main agent's own command as parent"

print("\nOn the wire (exclude_none=True):")
for call in (own_command, subagent_calls[0]):
    print(
        call.model_dump_json(
            exclude_none=True,
            include={"kind", "tool_call_id", "status", "title", "parent_tool_call_id"},
        )
    )
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"cost: {cost:.4f}")
