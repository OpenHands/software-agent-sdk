"""
Minimal example demonstrating ACPAgent usage with the OpenHands SDK.

This example shows how to use ACPAgent to interact with ACP servers
(like Claude-Code or Gemini CLI) through the Agent-Client Protocol.

Prerequisites:
    - An ACP server command available (e.g., npx -y claude-code-acp)
    - API keys for the ACP server if required (managed by the ACP server)

Note:
    - ACPAgent does NOT require an LLM parameter - the ACP server manages its own LLM
    - This is a minimal implementation. Many advanced SDK features
      (microagents, custom tools, security analyzers) are not yet supported.
"""

import os

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.event import MessageEvent


# Create an ACPAgent that connects to an ACP server
# Example 1: Using Claude-Code ACP server (requires claude-code-acp to be available)
agent = ACPAgent(
    acp_command=["npx", "-y", "claude-code-acp"],
    acp_args=[],  # Add any additional arguments for the ACP server
)

# Example 2: Using a local ACP server script
# agent = ACPAgent(
#     acp_command=["python", "/path/to/your/acp_server.py"],
#     acp_args=["--verbose"],
# )

# Example 3: Using Gemini CLI ACP server (hypothetical)
# agent = ACPAgent(
#     acp_command=["gemini-cli-acp"],
#     acp_args=[],
# )

# Create a conversation with the agent
cwd = os.getcwd()
conversation = LocalConversation(agent=agent, workspace=cwd)

# Send a message to the agent
print("Sending message to ACP agent...")
conversation.send_message("Hello! Please tell me about the Agent-Client Protocol.")

# Run the conversation
print("Running conversation...")
conversation.run()

# Access the events from the conversation state
print("\nConversation completed!")
print(f"Total events: {len(conversation.state.events)}")

# Print the last assistant message event
for event in reversed(list(conversation.state.events)):
    if isinstance(event, MessageEvent) and event.source == "agent":
        if event.llm_message and event.llm_message.content:
            for content in event.llm_message.content:
                print(f"\nAgent response: {content}")
        break

print("\nNote: The ACP server subprocess will be automatically cleaned up.")
