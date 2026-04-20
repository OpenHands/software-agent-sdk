"""Example: Using ACPAgent with Claude Code ACP server.

This example shows how to use an ACP-compatible server (claude-agent-acp)
as the agent backend instead of direct LLM calls.  It also demonstrates
``ask_agent()`` — a stateless side-question that forks the ACP session
and leaves the main conversation untouched.

Prerequisites:
    - Node.js / npx available
    - ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY set (can point to LiteLLM proxy)

Usage:
    uv run python examples/01_standalone_sdk/40_acp_agent_example.py
"""

import os

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation


agent = ACPAgent(acp_command=["npx", "-y", "@agentclientprotocol/claude-agent-acp"])

try:
    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)

    # --- Main conversation turn ---
    conversation.send_message(
        "List the Python source files under openhands-sdk/openhands/sdk/agent/, "
        "then read the __init__.py and summarize what agent classes are exported."
    )
    conversation.run()

    # --- ask_agent: stateless side-question via fork_session ---
    print("\n--- ask_agent ---")
    response = conversation.ask_agent(
        "Based on what you just saw, which agent class is the newest addition?"
    )
    print(f"ask_agent response: {response}")
    # Report cost (ACP server reports usage via session_update notifications)
    cost = agent.llm.metrics.accumulated_cost
    print(f"EXAMPLE_COST: {cost:.4f}")
finally:
    # Clean up the ACP server subprocess
    agent.close()

cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"\nEXAMPLE_COST: {cost}")
print("Done!")
