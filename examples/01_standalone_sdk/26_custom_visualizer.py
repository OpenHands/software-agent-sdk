"""Custom Visualizer Example

This example demonstrates how to create and use a custom visualizer by subclassing
ConversationVisualizer. This approach provides:
- Clean, testable code with class-based state management
- Direct configuration (just pass the visualizer instance to visualize parameter)
- Reusable visualizer that can be shared across conversations
- Better separation of concerns compared to callback functions

The MinimalProgressVisualizer produces concise output showing:
- LLM call completions
- Tool execution steps with command/path details
- Agent thinking indicators
- Error messages

This demonstrates the new API improvement where you can pass a ConversationVisualizer
instance directly to the visualize parameter instead of using callbacks.
"""

import logging
import os

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizer
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.tools.preset.default import get_default_agent


class MinimalProgressVisualizer(ConversationVisualizer):
    """A minimal progress visualizer that shows step counts and tool names.

    This visualizer produces concise output showing:
    - LLM call completions
    - Tool execution steps with command/path details
    - Agent thinking indicators
    - Error messages

    Example output:
        🤖 LLM call completed
        Step 1: Executing str_replace_editor (view: .../FACTS.txt)... ✓
        💭 Agent thinking...
        🤖 LLM call completed
        Step 2: Executing str_replace_editor (str_replace: .../FACTS.txt)... ✓
    """

    def __init__(self, **kwargs):
        """Initialize the minimal progress visualizer.

        Args:
            **kwargs: Additional arguments passed to ConversationVisualizer.
                     Note: We override visualization, so most ConversationVisualizer
                     parameters are ignored, but we keep the signature for
                     compatibility.
        """
        # Initialize parent but we'll override on_event
        # We don't need the console/panels from the parent
        super().__init__(**kwargs)

        # Track state for minimal progress output
        self._step_count = 0
        self._pending_action = False
        self._seen_llm_response_ids: set[str] = set()

    def on_event(self, event) -> None:
        """Handle events and produce minimal progress output."""
        if isinstance(event, ActionEvent):
            self._handle_action_event(event)
        elif isinstance(event, ObservationEvent):
            self._handle_observation_event()
        elif isinstance(event, AgentErrorEvent):
            self._handle_error_event(event)
        elif isinstance(event, MessageEvent):
            self._handle_message_event(event)

    def _handle_action_event(self, event: ActionEvent) -> None:
        """Handle ActionEvent - track LLM calls and show tool execution."""
        # Track LLM calls by monitoring new llm_response_id values
        if (
            event.llm_response_id
            and event.llm_response_id not in self._seen_llm_response_ids
        ):
            self._seen_llm_response_ids.add(event.llm_response_id)
            # This is a new LLM call - show it completed
            if not self._pending_action:
                print("🤖 LLM call completed", flush=True)

        # If previous action hasn't completed, complete it first
        if self._pending_action:
            print(" ✓", flush=True)

        self._step_count += 1
        tool_name = event.tool_name if event.tool_name else "unknown"

        # Extract command/action details if available
        action_details = ""
        if event.action:
            action_dict = (
                event.action.model_dump() if hasattr(event.action, "model_dump") else {}
            )
            if "command" in action_dict:
                command = action_dict["command"]
                # Show file path if available (for file operations)
                path = action_dict.get("path", "")
                if path:
                    # Truncate long paths
                    if len(path) > 40:
                        path = "..." + path[-37:]
                    action_details = f" ({command}: {path})"
                else:
                    action_details = f" ({command})"

        # Show step number and tool being executed on its own line
        print(
            f"Step {self._step_count}: Executing {tool_name}{action_details}...",
            end="",
            flush=True,
        )
        self._pending_action = True

    def _handle_observation_event(self) -> None:
        """Handle ObservationEvent - show completion indicator."""
        if self._pending_action:
            print(" ✓", flush=True)
            self._pending_action = False

    def _handle_error_event(self, event: AgentErrorEvent) -> None:
        """Handle AgentErrorEvent - show errors."""
        if self._pending_action:
            print(" ✗", flush=True)  # Mark previous action as failed
            self._pending_action = False

        error_msg = event.error
        # Truncate long error messages
        error_preview = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
        print(f"⚠️  Error: {error_preview}", flush=True)

    def _handle_message_event(self, event: MessageEvent) -> None:
        """Handle MessageEvent - track LLM calls and show thinking indicators."""
        # Track LLM calls from MessageEvent (agent messages without tool calls)
        if (
            event.source == "agent"
            and event.llm_response_id
            and event.llm_response_id not in self._seen_llm_response_ids
        ):
            self._seen_llm_response_ids.add(event.llm_response_id)
            # This is a new LLM call - show it completed
            if not self._pending_action:
                print("🤖 LLM call completed", flush=True)

        # Show when agent is "thinking" (making LLM calls between actions)
        if event.source == "agent" and event.llm_message.role == "assistant":
            # Agent is thinking/planning - show a thinking indicator
            if not self._pending_action:
                # Only show if we haven't already shown the LLM call completion
                if (
                    not event.llm_response_id
                    or event.llm_response_id in self._seen_llm_response_ids
                ):
                    print("💭 Agent thinking...", flush=True)


def main():
    # ============================================================================
    # Configure LLM and Agent
    # ============================================================================
    # You can get an API key from https://app.all-hands.dev/settings/api-keys
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
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

    # Create custom visualizer instance
    minimal_visualizer = MinimalProgressVisualizer()

    # Start a conversation with custom visualizer
    # NEW API: You can now pass a ConversationVisualizer instance directly!
    # This is much cleaner than the old approach of:
    #   visualize=False, callbacks=[custom_visualizer.on_event]
    cwd = os.getcwd()
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
        visualize=minimal_visualizer,  # Direct and clear!
    )

    # Send a message and let the agent run
    print("Sending task to agent...")
    conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
    conversation.run()
    print("Task completed!")

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"EXAMPLE_COST: ${cost:.4f}")

    # ============================================================================
    # API Improvement Summary
    # ============================================================================
    print("\n" + "=" * 80)
    print("🎉 API IMPROVEMENT DEMONSTRATED")
    print("=" * 80)
    print("OLD WAY (confusing):")
    print("  conversation = Conversation(")
    print("      agent=agent,")
    print("      workspace=cwd,")
    print("      visualize=False,  # Confusing: we DO want visualization!")
    print("      callbacks=[custom_visualizer.on_event],")
    print("  )")
    print()
    print("NEW WAY (clear and direct):")
    print("  conversation = Conversation(")
    print("      agent=agent,")
    print("      workspace=cwd,")
    print("      visualize=custom_visualizer,  # Direct and clear!")
    print("  )")
    print("=" * 80)


if __name__ == "__main__":
    main()
