"""Smoke script for Part 2 (progressive-disclosure `context: fork` via invoke_skill).

Demonstrates:
- A trigger-less fork skill (previously rejected by `_validate_context`).
- The skill advertised in `<available_skills>` with the fork marker.
- The agent invoking it via `invoke_skill(name=...)`.
- Only the subagent's final summary coming back — the raw skill body
  never enters the parent conversation.

Run:
    LLM_API_KEY=... uv run python .pr/smoke_invoke_skill_fork.py
"""

import os
import sys

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation
from openhands.sdk.skills import Skill


api_key = os.getenv("LLM_API_KEY")
if not api_key:
    sys.exit("Set LLM_API_KEY to run this smoke script.")

# Trigger-less fork skill. Before Part 2 this combination (context=fork +
# trigger=None) was rejected. The only way to invoke it is `invoke_skill`.
fork_skill = Skill(
    name="joke-teller",
    description="Tells one short, original joke.",
    content=(
        "Tell exactly one short, original joke (two lines at most). "
        "Make it different each time you are invoked. "
        "Reply with just the joke, nothing else. Do not explain it."
    ),
    is_agentskills_format=True,
    context="fork",
    trigger=None,
)

llm = LLM(
    usage_id="smoke",
    model=os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)
agent = Agent(llm=llm, tools=[], agent_context=AgentContext(skills=[fork_skill]))
conversation = Conversation(agent=agent, workspace=os.getcwd())

conversation.send_message(
    "Invoke the `joke-teller` skill (via invoke_skill) and show me the joke."
)
conversation.run()

# Sanity check: the parent history must not contain the raw skill body.
raw_body_marker = "different each time you are invoked"
all_text = "\n".join(str(getattr(e, "content", "")) for e in conversation.state.events)
assert raw_body_marker not in all_text, (
    "Fork skill body leaked into parent conversation — Part 2 invariant broken."
)
print("\n[smoke] OK — fork body did not leak into parent conversation.")
print(f"[smoke] invoked_skills: {conversation.state.invoked_skills}")
print(f"[smoke] cost: ${llm.metrics.accumulated_cost:.4f}")
