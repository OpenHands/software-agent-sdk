"""Browser Session Recording Example

This example demonstrates how to use the browser session recording feature
to capture and save a recording of the agent's browser interactions using rrweb.

The recording can be replayed later using rrweb-player to visualize the agent's
browsing session.

The recording will be automatically saved to the persistence directory when
browser_stop_recording is called. You can replay it with:
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
from openhands.tools.browser_use.definition import BROWSER_RECORDING_OUTPUT_DIR


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
    Tool(name=BrowserToolSet.name),
]

# Agent
agent = Agent(llm=llm, tools=tools)

llm_messages = []  # collect raw LLM messages


def conversation_callback(event: Event):
    if isinstance(event, LLMConvertibleEvent):
        llm_messages.append(event.to_llm_message())


# Create conversation with persistence_dir set to save browser recordings
conversation = Conversation(
    agent=agent,
    callbacks=[conversation_callback],
    workspace=cwd,
    persistence_dir="./.conversations",
)

# The prompt instructs the agent to:
# 1. Start recording the browser session
# 2. Browse to a website and perform some actions
# 3. Stop recording (auto-saves to file)
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

4. Finally, use `browser_stop_recording` to stop the recording.
   Events are automatically saved.
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

# Check if the recording files were created
# Recordings are saved in BROWSER_RECORDING_OUTPUT_DIR/recording-{timestamp}/
if os.path.exists(BROWSER_RECORDING_OUTPUT_DIR):
    # Find recording subdirectories (they start with "recording-")
    recording_dirs = sorted(
        [
            d
            for d in os.listdir(BROWSER_RECORDING_OUTPUT_DIR)
            if d.startswith("recording-")
            and os.path.isdir(os.path.join(BROWSER_RECORDING_OUTPUT_DIR, d))
        ]
    )

    if recording_dirs:
        # Process the most recent recording directory
        latest_recording = recording_dirs[-1]
        recording_path = os.path.join(BROWSER_RECORDING_OUTPUT_DIR, latest_recording)
        json_files = sorted(
            [f for f in os.listdir(recording_path) if f.endswith(".json")]
        )

        print(f"\n✓ Recording saved to: {recording_path}")
        print(f"✓ Number of files: {len(json_files)}")

        # Count total events across all files
        total_events = 0
        all_event_types: dict[int | str, int] = {}
        total_size = 0

        for json_file in json_files:
            filepath = os.path.join(recording_path, json_file)
            file_size = os.path.getsize(filepath)
            total_size += file_size

            with open(filepath) as f:
                events = json.load(f)

            # Events are stored as a list in each file
            if isinstance(events, list):
                total_events += len(events)
                for event in events:
                    event_type = event.get("type", "unknown")
                    all_event_types[event_type] = all_event_types.get(event_type, 0) + 1

            print(f"  - {json_file}: {len(events)} events, {file_size} bytes")

        print(f"✓ Total events: {total_events}")
        print(f"✓ Total size: {total_size} bytes")
        if all_event_types:
            print(f"✓ Event types: {all_event_types}")

        print("\nTo replay this recording, you can use:")
        print(
            "  - rrweb-player: "
            "https://github.com/rrweb-io/rrweb/tree/master/packages/rrweb-player"
        )
    else:
        print(f"\n✗ No recording directories found in: {BROWSER_RECORDING_OUTPUT_DIR}")
        print("  The agent may not have completed the recording task.")
else:
    print(f"\n✗ Observations directory not found: {BROWSER_RECORDING_OUTPUT_DIR}")
    print("  The agent may not have completed the recording task.")

print("\n" + "=" * 100)
print("Conversation finished.")
print(f"Total LLM messages: {len(llm_messages)}")
print("=" * 100)

# Report cost
cost = conversation.conversation_stats.get_combined_metrics().accumulated_cost
print(f"Conversation ID: {conversation.id}")
print(f"EXAMPLE_COST: {cost}")
