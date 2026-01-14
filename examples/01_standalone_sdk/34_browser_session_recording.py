"""Browser Session Recording Example

This example demonstrates how to use the browser session recording feature
to capture and save a recording of the agent's browser interactions using rrweb.

The recording can be replayed later using rrweb-player to visualize the agent's
browsing session.

Usage:
    # Set your LLM API key
    export LLM_API_KEY=your_api_key_here

    # Optionally set model (defaults to claude-sonnet)
    export LLM_MODEL=anthropic/claude-sonnet-4-5-20250929

    # Run the example
    python 34_browser_session_recording.py

The recording will be saved to ./browser_recording.json and can be replayed with:
    - rrweb-player: https://github.com/rrweb-io/rrweb/tree/master/packages/rrweb-player
    - Online viewer: https://www.rrweb.io/demo/
"""

import json
import os

from pydantic import SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LLMConvertibleEvent,
    get_logger,
)
from openhands.sdk.tool import Tool
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


logger = get_logger(__name__)

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

# Tools - including browser tools with recording capability
cwd = os.getcwd()
tools = [
    Tool(name=TerminalTool.name),
    Tool(name=FileEditorTool.name),
    Tool(name=BrowserToolSet.name),
]

# Agent
agent = Agent(llm=llm, tools=tools)

llm_messages = []  # collect raw LLM messages


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


conversation = Conversation(
    agent=agent, callbacks=[conversation_callback], workspace=cwd
)

# The prompt instructs the agent to:
# 1. Start recording the browser session
# 2. Browse to a website and perform some actions
# 3. Stop recording and save the recording
PROMPT = """
Please complete the following task to demonstrate browser session recording:

1. First, use `browser_start_recording` to begin recording the browser session.

2. Then navigate to https://docs.openhands.dev/ and:
   - Get the page content
   - Scroll down the page
   - Get the browser state to see interactive elements

3. Next, navigate to https://docs.openhands.dev/openhands/usage/cli/installation and:
   - Get the page content
   - Scroll down to see more content

4. Finally, use `browser_stop_recording` to stop the recording and retrieve the 
   captured events.

5. Save the recording JSON to a file called 'browser_recording.json' in the 
   current directory.

Please report what was recorded (number of events, types of events, etc.).
"""

print("=" * 80)
print("Browser Session Recording Example")
print("=" * 80)
print("\nTask: Record an agent's browser session and save it for replay")
print("\nStarting conversation with agent...\n")

conversation.send_message(PROMPT)
conversation.run()

print("\n" + "=" * 80)
print("Conversation finished!")
print("=" * 80)

# Check if the recording file was created
recording_file = os.path.join(cwd, "browser_recording.json")
if os.path.exists(recording_file):
    with open(recording_file) as f:
        recording_data = json.load(f)

    print(f"\n✓ Recording saved to: {recording_file}")
    print(f"✓ Number of events: {recording_data.get('count', len(recording_data.get('events', [])))}")
    print(f"✓ File size: {os.path.getsize(recording_file)} bytes")

    # Show event types
    events = recording_data.get("events", [])
    if events:
        event_types = {}
        for event in events:
            event_type = event.get("type", "unknown")
            event_types[event_type] = event_types.get(event_type, 0) + 1
        print(f"✓ Event types: {event_types}")

    print("\nTo replay this recording, you can use:")
    print("  - rrweb-player: https://github.com/rrweb-io/rrweb/tree/master/packages/rrweb-player")
    print("  - Online viewer: https://www.rrweb.io/demo/")
else:
    print(f"\n✗ Recording file not found at: {recording_file}")
    print("  The agent may not have completed the recording task.")

print("\n" + "=" * 100)
print("Conversation finished.")
print(f"Total LLM messages: {len(llm_messages)}")
print("=" * 100)

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"EXAMPLE_COST: {cost}")
