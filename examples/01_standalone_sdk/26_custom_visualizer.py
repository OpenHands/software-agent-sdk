"""Custom Visualizer Example

This example demonstrates how to create and use a custom visualizer by subclassing
ConversationVisualizer. This approach provides:
- Clean, testable code with class-based state management
- Direct configuration (just pass the visualizer instance to visualize parameter)
- Reusable visualizer that can be shared across conversations
- Better separation of concerns compared to callback functions
- Event handler registration to avoid long if/elif chains

The MinimalProgressVisualizer produces concise output showing:
- LLM call completions
- Tool execution steps with command/path details
- Agent thinking indicators
- Error messages

This demonstrates how you can pass a ConversationVisualizer instance directly
to the visualize parameter for clean, reusable visualization logic.
"""

import logging
import os
from collections.abc import Callable

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizer
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    MessageEvent,
    ObservationEvent,
)
from openhands.tools.preset.default import get_default_agent


def handles(event_type: type[Event]):
    """Decorator to register a method as an event handler."""

    def decorator(func):
        func._handles_event_type = event_type
        return func

    return decorator


class EventHandlerMixin:
    """Mixin that provides event handler registration via decorators."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._event_handlers: dict[type[Event], Callable[[Event], None]] = {}
        self._register_handlers()

    def _register_handlers(self):
        """Automatically discover and register event handlers."""
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if hasattr(attr, "_handles_event_type"):
                event_type = attr._handles_event_type
                self._event_handlers[event_type] = attr

    def on_event(self, event: Event) -> None:
        """Dispatch events to registered handlers."""
        event_type = type(event)
        handler = self._event_handlers.get(event_type)
        if handler:
            handler(event)
        # Optionally handle unknown events - subclasses can override this
        else:
            self._handle_unknown_event(event)

    def _handle_unknown_event(self, event: Event) -> None:
        """Handle unknown event types. Override in subclasses if needed."""
        # Default: do nothing for unknown events
        pass


class MinimalProgressVisualizer(EventHandlerMixin, ConversationVisualizer):
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

    # Event handlers are now registered via decorators - no need for on_event override

    @handles(ActionEvent)
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

    @handles(ObservationEvent)
    def _handle_observation_event(self, event: ObservationEvent) -> None:
        """Handle ObservationEvent - show completion indicator."""
        _ = event  # Event parameter required for handler signature
        if self._pending_action:
            print(" ✓", flush=True)
            self._pending_action = False

    @handles(AgentErrorEvent)
    def _handle_error_event(self, event: AgentErrorEvent) -> None:
        """Handle AgentErrorEvent - show errors."""
        if self._pending_action:
            print(" ✗", flush=True)  # Mark previous action as failed
            self._pending_action = False

        error_msg = event.error
        # Truncate long error messages
        error_preview = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
        print(f"⚠️  Error: {error_preview}", flush=True)

    @handles(MessageEvent)
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
    cwd = os.getcwd()
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
        visualize=minimal_visualizer,
    )

    # Send a message and let the agent run
    print("Sending task to agent...")
    conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
    conversation.run()
    print("Task completed!")

    # Report cost
    cost = llm.metrics.accumulated_cost
    print(f"EXAMPLE_COST: ${cost:.4f}")


if __name__ == "__main__":
    main()
