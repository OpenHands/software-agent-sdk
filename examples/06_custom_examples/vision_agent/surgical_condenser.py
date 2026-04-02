"""
OpenHands Agent SDK - Large File Surgical Condenser Example

This example demonstrates the use of `LargeFileSurgicalCondenser` to manage
context window bloat caused by large tool outputs, specifically images from
the `FileEditorTool`.

The Surgical Condenser follows a "Post-Inference Cleanup" strategy:
1. Turn 1 (Action): Agent views a 1MB image. LLM sees the raw image data for analysis.
2. Turn 1 (Inference): Agent responds with its analysis.
3. Turn 2 (Start): Condenser detects that the image-heavy observation has
   been "processed" (followed by an agent response).
4. Turn 2 (Surgical Action): It replaces the raw image data with a concise
   summary string like "[Surgical Condensation] Viewed image/data" in the history.
5. Turn 2 (Inference): The conversation continues with a lean, summary-based history,
   keeping KV cache hit rates high.
"""

import os

from pydantic import SecretStr

# Import necessary SDK components
from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    Event,
    LargeFileSurgicalCondenser,
    LLMSummarizingCondenser,
    PipelineCondenser,
    Tool,
    get_logger,
)
from openhands.sdk.event.condenser import Condensation
from openhands.tools.file_editor import FileEditorTool


logger = get_logger(__name__)


def main():
    # 1. Configure LLM
    # Make sure to set LLM_API_KEY environment variable.
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("Error: LLM_API_KEY environment variable not set.")
        print(
            "Please set it before running this example: export LLM_API_KEY='your-key'"
        )
        return

    # Use a vision-capable model
    model = os.getenv("LLM_MODEL", "anthropic/claude-3-5-sonnet-20240620")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    if not llm.vision_is_active():
        print(f"Warning: The model {model} might not support vision input.")
        print("The surgical condenser will still work if the tool outputs large text,")
        print("but image viewing requires vision support.")

    # It targets 'file_editor' and condenses outputs larger than 1KB
    # or containing images.
    surgical_condenser = LargeFileSurgicalCondenser(
        threshold_bytes=1024,  # 1KB
        target_tool="file_editor",
    )

    # Chaining with a general summarizer using PipelineCondenser for a production setup
    pipeline = PipelineCondenser(
        condensers=[
            surgical_condenser,
            # Standard summarizer for general windowing after 50 events
            LLMSummarizingCondenser(
                llm=llm.model_copy(update={"usage_id": "condenser"}), max_size=50
            ),
        ]
    )

    # 3. Initialize Agent with the Condenser Pipeline
    agent = Agent(
        llm=llm,
        tools=[Tool(name=FileEditorTool.name)],
        condenser=pipeline,
    )

    # 4. Setup Conversation and Track Events
    events_log = []

    def track_events(event: Event):
        # We collect all events in the conversation to verify condensation
        events_log.append(event)
        # Optional: print summary of each event as it occurs
        if hasattr(event, "visualize"):
            print(f"\n[EVENT] {event.__class__.__name__}:")
            print("-" * 20)
            print(event.visualize)

    workspace = os.getcwd()
    conversation = Conversation(
        agent=agent, workspace=workspace, callbacks=[track_events]
    )

    # 5. Execute Vision Task
    image_name = "sample.png"
    sample_image = os.path.abspath(os.path.join(os.path.dirname(__file__), image_name))

    if not os.path.exists(sample_image):
        print(f"Error: Sample image not found at {sample_image}")
        return

    print("=" * 100)
    print("STIRRING SURGICAL CONDENSER EXAMPLE")
    print(f"Analyzing Image: {image_name}")
    print("=" * 100)

    # TURN 1: Initial View and Inference
    # The agent will use FileEditorTool to view the image. This puts the large
    # image data in history.
    print("[Turn 1] Requesting image analysis...")
    conversation.send_message(
        f"Please view the image at {sample_image} and describe its contents."
    )
    conversation.run()

    # TURN 2: Follow-up question
    # This turn initiates the second LLM call. Before the call, the surgical condenser
    # detects the previous turn's bloated image data and condenses it into a summary.
    print("\n[Turn 2] Follow-up question (Triggering Surgical Condenser)...")
    conversation.send_message(
        "Based on your previous analysis, what would you suggest "
        "as a title for this image?"
    )
    conversation.run()

    # 6. Verify Condensation
    print("\n" + "=" * 100)
    print("VERIFICATION: Checking event history for condensation action")
    print("=" * 100)

    # Look for Condensation events in our tracked log
    condensation_events = [e for e in events_log if isinstance(e, Condensation)]

    if condensation_events:
        print(
            f"Success! Found {len(condensation_events)} "
            f"condensation event(s) in history."
        )
        for i, ce in enumerate(condensation_events):
            print(f"\nCondensation Event {i + 1}:")
            print(f"  Summary: {ce.summary}")
            print(f"  Forgotten Event IDs: {ce.forgotten_event_ids}")
            print(f"  Index (Summary Offset): {ce.summary_offset}")
            print(
                "\n  Effect: The 150KB+ image data was surgically removed and replaced "
            )
            print("  with the summary above BEFORE the Turn 2 LLM call was made.")
            print("  This kept Turn 2's context lean and KV-cache friendly!")
    else:
        print("Note: No condensation event found yet.")
        print(
            "Verify your LLM returned an ActionEvent using 'file_editor' "
            "with command='view'."
        )

    print("\nExample complete.")


if __name__ == "__main__":
    main()
