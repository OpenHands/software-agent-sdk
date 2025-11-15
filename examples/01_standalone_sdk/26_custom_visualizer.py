"""Custom Visualizer Example

This example demonstrates how to create and use a custom visualizer by subclassing
ConversationVisualizer. This approach provides:
- Clean, testable code with class-based state management
- Direct configuration (just pass the visualizer instance to visualizer parameter)
- Reusable visualizer that can be shared across conversations

This demonstrates how you can pass a ConversationVisualizer instance directly
to the visualizer parameter for clean, reusable visualization logic.
"""

import logging
import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizerBase
from openhands.sdk.event import (
    Event,
)
from openhands.tools.preset.default import get_default_agent


class MinimalVisualizer(ConversationVisualizerBase):
    """A minimal visualizer that print the raw events as they occur."""

    def on_event(self, event: Event) -> None:
        """Handle events for minimal progress visualization."""
        print(f"\n\n[EVENT] {type(event).__name__}: {event.model_dump_json()[:200]}...")


api_key = os.getenv("LLM_API_KEY")
assert api_key is not None, "LLM_API_KEY environment variable is not set."
model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
base_url = os.getenv("LLM_BASE_URL")
llm = LLM(
    model=model,
    api_key=SecretStr(api_key),
    base_url=base_url,
    usage_id="agent",
)
agent = get_default_agent(llm=llm, cli_mode=True)

# ============================================================================
# Configure Visualization
# ============================================================================
# Set logging level to reduce verbosity
logging.getLogger().setLevel(logging.WARNING)

# Start a conversation with custom visualizer
cwd = os.getcwd()
conversation = Conversation(
    agent=agent,
    workspace=cwd,
    visualizer=MinimalVisualizer(),
)

# Send a message and let the agent run
print("Sending task to agent...")
conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
conversation.run()
print("Task completed!")

# Report cost
cost = llm.metrics.accumulated_cost
print(f"EXAMPLE_COST: ${cost:.4f}")
