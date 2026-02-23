"""Example: ACPAgent with TCP transport.

This example shows how to connect ACPAgent to an already-running ACP server
over TCP, instead of spawning one as a subprocess.

Note: TCP is a *custom transport* — the ACP spec currently defines stdio
(and draft Streamable HTTP).  The remote server must speak newline-delimited
JSON-RPC over a TCP socket (stdio-style framing).

Prerequisites:
    - An ACP-compatible server listening on TCP with newline-delimited
      JSON-RPC (e.g. a wrapper around claude-code-acp)
    - ACP_HOST env var (hostname, e.g. "acp-server.internal")
    - ACP_PORT env var (port, default 4001)

Usage:
    ACP_HOST=localhost ACP_PORT=4001 \
        uv run python examples/01_standalone_sdk/41_acp_agent_remote_example.py
"""

import os

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation


acp_host = os.environ["ACP_HOST"]
acp_port = int(os.getenv("ACP_PORT", "4001"))

agent = ACPAgent(acp_host=acp_host, acp_port=acp_port)

try:
    cwd = os.getcwd()
    conversation = Conversation(agent=agent, workspace=cwd)

    conversation.send_message(
        "List the files in the current directory and write a short "
        "summary of what you see into SUMMARY.md."
    )
    conversation.run()

    # --- ask_agent: stateless side-question via fork_session ---
    print("\n--- ask_agent ---")
    response = conversation.ask_agent(
        "Based on what you just saw, what is the most interesting file?"
    )
    print(f"ask_agent response: {response}")
finally:
    agent.close()

print("Done!")
