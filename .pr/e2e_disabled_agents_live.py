"""Live e2e for disabled_agents: a real model hits the refusal, then recovers.

Main agent gets ONLY the task tool, with code-explorer on the deny-list.
Asked to use code-explorer, it should hit the spawn-time refusal (visible to
the model as a tool error), then recover by picking an allowed type, which
spawns a real sub-agent that answers. Run from the repo checkout:

    LLM_API_KEY=$(pass ai/zai) uv run python <this file>
"""

import os
import tempfile

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Tool
from openhands.tools.preset import register_builtins_agents
from openhands.tools.task import TaskToolSet


register_builtins_agents()

llm = LLM(
    model="openai/glm-4.7",
    api_key=os.environ["LLM_API_KEY"],
    base_url="https://api.z.ai/api/coding/paas/v4",
    usage_id="e2e-disabled-agents",
)

agent = Agent(
    llm=llm,
    tools=[Tool(name=TaskToolSet.name)],
    agent_context=AgentContext(disabled_agents=["code-explorer"]),
)

conv = Conversation(
    agent=agent,
    workspace=tempfile.mkdtemp(prefix="e2e_disabled_agents_"),
    visualizer=None,
)

conv.send_message(
    "Use the task tool with subagent_type='code-explorer' to answer this "
    "question: what is 17 * 23? Report the answer when you have it."
)
conv.run()

print("=== E2E RESULT ===")
task_calls = []
refusals = []
for event in conv.state.events:
    kind = type(event).__name__
    if kind == "ActionEvent" and hasattr(event, "action"):
        st = getattr(event.action, "subagent_type", None)
        if st is not None:
            task_calls.append(st)
    observation = getattr(event, "observation", None)
    text = getattr(observation, "text", "") or getattr(event, "text", "") or ""
    if "disabled for this conversation" in text:
        refusals.append(text.strip())

print(f"task tool calls by subagent_type: {task_calls}")
print(f"refusals observed: {len(refusals)}")
for r in refusals:
    print(f"  -> {r[:200]}")

status = conv.state.execution_status
print(f"final status: {status}")
cost = conv.conversation_stats.get_combined_metrics().accumulated_cost
print(f"cost: ${cost:.4f}")

final = ""
for event in reversed(list(conv.state.events)):
    if (
        type(event).__name__ == "MessageEvent"
        and getattr(event, "source", "") == "agent"
    ):
        final = str(event)[:400]
        break
print(f"last agent message: {final}")
