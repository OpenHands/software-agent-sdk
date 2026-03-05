"""Example: Using ACPAgent with Claude Code ACP server.

This example shows how to use an ACP-compatible server (claude-code-acp)
as the agent backend instead of direct LLM calls.  It also demonstrates
``ask_agent()`` — a stateless side-question that forks the ACP session
and leaves the main conversation untouched.

Prerequisites:
    - Node.js / npx available
    - Claude Code CLI authenticated (or CLAUDE_API_KEY set)

Usage:
    uv run python examples/01_standalone_sdk/40_acp_agent_example.py
"""

import logging
import os

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation

# Enable info logging for ACP agent to see cost fallback diagnostics
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout)

agent = ACPAgent(acp_command=["npx", "-y", "@zed-industries/claude-code-acp"])

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
finally:
    # Clean up the ACP server subprocess
    agent.close()

metrics = conversation.conversation_stats.get_combined_metrics()
cost = metrics.accumulated_cost
print(f"\nDEBUG_COST_INFO: accumulated_cost={cost}")
print(f"DEBUG_COST_INFO: costs_list={metrics.costs}")
print(f"DEBUG_COST_INFO: token_usages={[(t.prompt_tokens, t.completion_tokens) for t in (metrics.token_usages or [])]}")
print(f"DEBUG_COST_INFO: LLM_MODEL={os.environ.get('LLM_MODEL', 'NOT_SET')}")
print(f"\nEXAMPLE_COST: {cost}")
assert cost > 0, f"Expected non-zero cost, got {cost}"
print("Done!")
