"""Custom Visualizer Example

This example demonstrates how to create and use a custom visualizer by subclassing
ConversationVisualizer. This approach provides:
- Clean, testable code with class-based state management
- Direct configuration (just pass the visualizer instance to visualizer parameter)
- Reusable visualizer that can be shared across conversations

The MinimalProgressVisualizer produces concise output showing:
- LLM call completions with cost and token information
- Tool execution steps with command/path details
- Agent thinking indicators
- Error messages

This demonstrates how you can pass a ConversationVisualizer instance directly
to the visualizer parameter for clean, reusable visualization logic.
"""

import logging
import os
from collections.abc import Callable

from pydantic import SecretStr

from openhands.sdk import LLM, Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizerBase
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


class MinimalProgressVisualizer(EventHandlerMixin, ConversationVisualizerBase):
    """A minimal progress visualizer that shows step counts and tool names.

    This visualizer produces concise output showing:
    - LLM call completions with cost and token information
    - Tool execution steps with command/path details
    - Agent thinking indicators
    - Error messages

    Example output:
        🤖 LLM call completed (cost: $0.001234, tokens: prompt=100,
            completion=50, total=150)
        Step 1: Executing str_replace_editor (view: .../FACTS.txt)... ✓
        💭 Agent thinking...
        🤖 LLM call completed (cost: $0.002345, tokens: prompt=200,
            completion=100, total=300)
        Step 2: Executing str_replace_editor (str_replace: .../FACTS.txt)... ✓
    """

    def __init__(self, name: str | None = None):
        """Initialize the minimal progress visualizer.

        Args:
            name: Optional name to identify the agent/conversation.
                                  Note: This simple visualizer doesn't use it in output,
                                  but accepts it for compatibility with the base class.
        """
        # Initialize parent - state will be set later via initialize()
        super().__init__(name=name)

        # Track state for minimal progress output
        self._event_counter = (
            0  # Sequential counter for all events (LLM calls and tools)
        )
        self._seen_llm_response_ids: set[str] = set()
        # Track which response IDs we've already displayed metrics for
        self._displayed_metrics_for_response_ids: set[str] = set()
        # Track which token usages we've already seen (by response_id)
        self._seen_token_usage_response_ids: set[str] = set()
        # Track which costs we've already seen (by index)
        self._seen_cost_count = 0

    def _get_metrics_for_response_id(
        self, response_id: str
    ) -> tuple[float, dict] | None:
        """Extract cost and token usage for a specific response_id.

        Gets metrics from conversation_stats, tracking incrementally to find
        new metrics.

        Returns:
            Tuple of (cost, token_info_dict) or None if not found.
            token_info_dict contains: prompt_tokens, completion_tokens, total_tokens
        """
        # Get metrics from conversation stats (source of truth)
        if not self.conversation_stats:
            return None

        combined_metrics = self.conversation_stats.get_combined_metrics()
        if not combined_metrics:
            return None

        # Find token usage for this response_id that we haven't seen yet
        token_usage = None
        token_usage_index = None
        for i, usage in enumerate(combined_metrics.token_usages):
            if (
                usage.response_id == response_id
                and usage.response_id not in self._seen_token_usage_response_ids
            ):
                token_usage = usage
                token_usage_index = i
                self._seen_token_usage_response_ids.add(usage.response_id)
                break

        if not token_usage:
            return None

        # Find the corresponding cost
        # Costs and token_usages are added in the same order, but costs may be
        # skipped if zero
        cost = 0.0

        # Look for new costs that we haven't seen yet
        if (
            combined_metrics.costs
            and len(combined_metrics.costs) > self._seen_cost_count
        ):
            # Get the cost at the same index as the token usage, or the most
            # recent new cost
            if token_usage_index is not None and token_usage_index < len(
                combined_metrics.costs
            ):
                cost = combined_metrics.costs[token_usage_index].cost
                self._seen_cost_count = max(
                    self._seen_cost_count,
                    token_usage_index + 1 if token_usage_index is not None else 0,
                )
            else:
                # Use the most recent cost if we have fewer costs than token usages
                cost = combined_metrics.costs[-1].cost
                self._seen_cost_count = len(combined_metrics.costs)

        return (
            cost,
            {
                "prompt_tokens": token_usage.prompt_tokens,
                "completion_tokens": token_usage.completion_tokens,
                "total_tokens": token_usage.prompt_tokens
                + token_usage.completion_tokens,
            },
        )

    def _format_llm_call_line(self, response_id: str) -> str | None:
        """Format LLM call line with cost and token information.

        Returns:
            Formatted string or None if already displayed.
        """
        if response_id in self._displayed_metrics_for_response_ids:
            return None

        metrics_info = self._get_metrics_for_response_id(response_id)
        if metrics_info:
            cost, token_info = metrics_info
            self._displayed_metrics_for_response_ids.add(response_id)

            # Format: "1. LLM call (tokens: 0000, cost $0.00)"
            total_tokens = token_info["total_tokens"]
            return f"LLM call (tokens: {total_tokens:04d}, cost ${cost:.2f})"

        # Fallback if metrics not available
        self._displayed_metrics_for_response_ids.add(response_id)
        return "LLM call (tokens: 0000, cost $0.00)"

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
            # This is a new LLM call - show it
            llm_line = self._format_llm_call_line(event.llm_response_id)
            if llm_line:
                self._event_counter += 1
                print(f"{self._event_counter}. {llm_line}", flush=True)

        # Show tool execution
        self._event_counter += 1
        tool_name = event.tool_name if event.tool_name else "unknown"

        # Extract command/action details if available
        command_str = ""
        path_str = ""
        if event.action:
            action_dict = (
                event.action.model_dump() if hasattr(event.action, "model_dump") else {}
            )
            if "command" in action_dict:
                command_str = action_dict["command"]
            if "path" in action_dict:
                path_str = action_dict.get("path", "")

        # Format: "2. Tool: file_editor:view path"
        if command_str and path_str:
            tool_line = f"Tool: {tool_name}:{command_str} {path_str}"
        elif command_str:
            tool_line = f"Tool: {tool_name}:{command_str}"
        else:
            tool_line = f"Tool: {tool_name}"

        print(f"{self._event_counter}. {tool_line}", flush=True)

    @handles(ObservationEvent)
    def _handle_observation_event(self, event: ObservationEvent) -> None:
        """Handle ObservationEvent - no output needed."""
        _ = event  # Event parameter required for handler signature

    @handles(AgentErrorEvent)
    def _handle_error_event(self, event: AgentErrorEvent) -> None:
        """Handle AgentErrorEvent - show errors."""
        self._event_counter += 1
        error_msg = event.error
        # Truncate long error messages
        error_preview = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
        print(f"{self._event_counter}. Error: {error_preview}", flush=True)

    @handles(MessageEvent)
    def _handle_message_event(self, event: MessageEvent) -> None:
        """Handle MessageEvent - track LLM calls."""
        # Track LLM calls from MessageEvent (agent messages without tool calls)
        if (
            event.source == "agent"
            and event.llm_response_id
            and event.llm_response_id not in self._seen_llm_response_ids
        ):
            self._seen_llm_response_ids.add(event.llm_response_id)
            # This is a new LLM call - show it
            llm_line = self._format_llm_call_line(event.llm_response_id)
            if llm_line:
                self._event_counter += 1
                print(f"{self._event_counter}. {llm_line}", flush=True)


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
        visualizer=minimal_visualizer,
    )

    # Send a message and let the agent run
    print("Sending task to agent...")
    conversation.send_message("Write 3 facts about the current project into FACTS.txt.")
    conversation.run()
    print("Task completed!")

    # Report final accumulated cost and tokens
    final_metrics = llm.metrics
    print("\n=== Final Summary ===")
    print(f"Total Cost: ${final_metrics.accumulated_cost:.2f}")
    if final_metrics.accumulated_token_usage:
        usage = final_metrics.accumulated_token_usage
        total_tokens = usage.prompt_tokens + usage.completion_tokens
        print(
            f"Total Tokens: prompt={usage.prompt_tokens}, "
            f"completion={usage.completion_tokens}, "
            f"total={total_tokens}"
        )


if __name__ == "__main__":
    main()
