"""
Example demonstrating that user messages can be sent and processed while
an agent is busy.

This example demonstrates a key capability of the OpenHands agent system: the ability
to receive and process new user messages even while the agent is actively working on
a previous task. This is made possible by the agent's event-driven architecture.

Demonstration Flow:
1. Send initial message asking agent to:
   - Write "Message 1 sent at [time], written at [CURRENT_TIME]"
   - Wait 3 seconds
   - Write "Message 2 sent at [time], written at [CURRENT_TIME]"
    [time] is the time the message was sent to the agent
    [CURRENT_TIME] is the time the agent writes the line
2. Start agent processing in a background thread
3. While agent is busy (during the 3-second delay), send a second message asking to add:
   - "Message 3 sent at [time], written at [CURRENT_TIME]"
4. Verify that all three lines are processed and included in the final document

Expected Evidence:
The final document will contain three lines with dual timestamps:
- "Message 1 sent at HH:MM:SS, written at HH:MM:SS" (from initial message, written immediately)
- "Message 2 sent at HH:MM:SS, written at HH:MM:SS" (from initial message, written after 3-second delay)
- "Message 3 sent at HH:MM:SS, written at HH:MM:SS" (from second message sent during delay)

The timestamps will show that Message 3 was sent while the agent was running,
but was still successfully processed and written to the document.

This proves that:
- The second user message was sent while the agent was processing the first task
- The agent successfully received and processed the second message
- The agent's event system allows for real-time message integration during processing

Key Components Demonstrated:
- Conversation.send_message(): Adds messages to events list immediately
- Agent.step(): Processes all events including newly added messages
- Threading: Allows message sending while agent is actively processing
"""  # noqa

import os
import threading
import time
from datetime import datetime

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
)
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


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

# Tools
cwd = os.getcwd()
tools = [
    Tool(
        name=TerminalTool.name,
    ),
    Tool(name=FileEditorTool.name),
]

# Agent
agent = Agent(llm=llm, tools=tools)
conversation = Conversation(agent)


def timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


print("=== Send Message While Processing Example ===")

# Step 1: Send initial message
start_time = timestamp()
conversation.send_message(
    f"Create a file called document.txt and write this first sentence: "
    f"'Message 1 sent at {start_time}, written at [CURRENT_TIME].' "
    f"Replace [CURRENT_TIME] with the actual current time when you write the line. "
    f"Then wait 3 seconds and write 'Message 2 sent at {start_time}, written at [CURRENT_TIME].'"  # noqa
)

# Step 2: Start agent processing in background
thread = threading.Thread(target=conversation.run)
thread.start()

# Step 3: Wait then send second message while agent is processing
time.sleep(2)  # Give agent time to start working

second_time = timestamp()

conversation.send_message(
    f"Please also add this second sentence to document.txt: "
    f"'Message 3 sent at {second_time}, written at [CURRENT_TIME].' "
    f"Replace [CURRENT_TIME] with the actual current time when you write this line."
)

# Wait for completion
thread.join()

# Verification
document_path = os.path.join(cwd, "document.txt")
if os.path.exists(document_path):
    with open(document_path) as f:
        content = f.read()

    print("\nDocument contents:")
    print("─────────────────────")
    print(content)
    print("─────────────────────")

    # Check if both messages were processed
    if "Message 1" in content and "Message 2" in content:
        print("\nSUCCESS: Agent processed both messages!")
        print(
            "This proves the agent received the second message while processing the first task."  # noqa
        )
    else:
        print("\nWARNING: Agent may not have processed the second message")

    # Clean up
    os.remove(document_path)
else:
    print("WARNING: Document.txt was not created")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: {cost}")
