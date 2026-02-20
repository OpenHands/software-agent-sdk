"""Example: Using ACPAgent with Claude Code ACP server.

This example shows how to use an ACP-compatible server (claude-code-acp)
as the agent backend instead of direct LLM calls.

Prerequisites:
    - Node.js / npx available
    - Claude Code CLI authenticated (or CLAUDE_API_KEY set)

Usage:
    uv run python examples/01_standalone_sdk/40_acp_agent_example.py
"""

import os

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation


agent = ACPAgent(acp_command=["npx", "-y", "claude-code-acp"])

try:
    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)

    conversation.send_message(
        "List the Python source files under openhands-sdk/openhands/sdk/agent/, "
        "then read the __init__.py and summarize what agent classes are exported."
    )
    conversation.run()
finally:
    # Clean up the ACP server subprocess
    agent.close()

print("Done!")
