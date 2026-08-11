"""Inspect task outcome when an agent is blocked by missing MCP tools.

This example intentionally asks the agent to do work that requires a Slack MCP
server, but the agent is created without any ``mcp_config``. Because
``FinishTool`` has a built-in structured task outcome response schema, the agent
can still finish and report that the task is blocked, including the blocker
reason. The SDK stores the latest reported outcome on
``conversation.state.task_outcome`` for callers to inspect after the run.
"""

import os

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."

llm = LLM(
    usage_id="agent",
    model=os.getenv("LLM_MODEL", "gpt-5.5"),
    api_key=SecretStr(api_key),
    base_url=os.getenv("LLM_BASE_URL"),
)

# No MCP servers are configured on purpose. The agent only has the default
# FinishTool and ThinkTool, so it cannot actually call Slack MCP tools.
agent = Agent(llm=llm, tools=[], mcp_config={})
conversation = Conversation(agent=agent, workspace=os.getcwd())

conversation.send_message(
    "Use the Slack MCP tools to list recent messages from #engineering and "
    "summarize the latest blocker. If no Slack MCP tools are available, finish "
    "and report a blocked task outcome explaining the missing integration."
)
conversation.run()

outcome = conversation.state.task_outcome
print("\n" + "=" * 70)
print("Latest task outcome")
print("=" * 70)
if outcome is None:
    print("No task outcome was reported.")
else:
    print(f"status: {outcome.status}")
    print(f"source: {outcome.source}")
    print(f"summary: {outcome.summary}")
    print(f"needs_user_action: {outcome.needs_user_action}")
    if outcome.blockers:
        print("blockers:")
        for blocker in outcome.blockers:
            print(f"- type: {blocker.type}")
            print(f"  message: {blocker.message}")
            print(f"  recoverable: {blocker.recoverable}")

expected = outcome is not None and outcome.status == "blocked"
print(f"\nReported blocked outcome: {expected}")
print("=" * 70)

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST: {cost}")
