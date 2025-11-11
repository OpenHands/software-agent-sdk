"""Custom Visualizer Example - Detailed LLM Metrics

This example builds on the custom visualizer example (26_custom_visualizer.py) by
showing how to compute and add detailed LLM metrics to the visualizer.
The TokenCountingVisualizer produces concise output showing:
- LLM call completions with latency, cost and token information
- Tool execution steps with command/path details
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
)
from openhands.sdk.llm.utils.metrics import Metrics, TokenUsage
from openhands.sdk.tool import Action
from openhands.tools.preset.default import get_default_agent


def handles(event_type: type[Event]):
    """Decorator to register a method as an event handler for a specific event type."""

    def decorator(func):
        func._handles_event_type = event_type
        return func

    return decorator


class MetricsCache:
    """Handles caching and lookup of metrics (latency, cost, tokens) by response_id.

    **What the SDK provides:**
    The SDK provides metrics in separate lists via `Metrics`:
    - `response_latencies`: List of ResponseLatency objects
      (always added, has response_id)
    - `token_usages`: List of TokenUsage objects (has response_id)
    - `costs`: List of Cost objects (only added if cost > 0, no response_id field)

    These lists are added in the same order, but costs may be skipped if zero.

    **What we need to do ourselves:**
    The SDK doesn't provide a direct way to get cost for a specific response_id because:
    1. Cost objects don't have a response_id field
    2. Costs may be skipped if zero, so indices don't align perfectly

    To match costs to response_ids, we use the ResponseLatency index since:
    1. ResponseLatency is always added (unlike costs which skip zeros)
    2. ResponseLatency has a response_id field for direct matching
    3. Costs and response_latencies are added in the same order

    This class builds a cache mapping response_id -> (latency, latency_index,
    token_usage) and incrementally updates it as new metrics are added,
    avoiding full rebuilds.
    """

    def __init__(self) -> None:
        """Initialize the metrics cache."""
        # Cache: response_id -> (latency, latency_index, token_usage)
        self._cache: dict[str, tuple[float, int, TokenUsage]] = {}
        self._last_processed_count: int = 0

    def get_metrics(
        self, response_id: str, combined_metrics: Metrics
    ) -> tuple[float, float, dict] | None:
        """Get latency, cost, and token usage for a specific response_id.

        Args:
            response_id: The response ID to look up
            combined_metrics: The metrics object containing all metrics

        Returns:
            Tuple of (latency, cost, token_info_dict) or None if not found.
            token_info_dict contains: prompt_tokens, completion_tokens, total_tokens
        """
        # Update cache if new entries have been added
        self._update_cache(combined_metrics)

        # Lookup from cache
        cached = self._cache.get(response_id)
        if not cached:
            return None

        latency, latency_index, token_usage = cached

        # Match cost using latency_index
        # Since response_latencies and costs are added in the same order
        # (with costs skipping zeros), we can use the latency_index to get the
        # corresponding cost.
        cost = 0.0
        if latency_index >= 0 and combined_metrics.costs:
            if latency_index < len(combined_metrics.costs):
                cost = combined_metrics.costs[latency_index].cost
            # If latency_index is beyond costs list, this response_id had zero
            # cost (not recorded)

        return (
            latency,
            cost,
            {
                "prompt_tokens": token_usage.prompt_tokens,
                "completion_tokens": token_usage.completion_tokens,
                "total_tokens": token_usage.prompt_tokens
                + token_usage.completion_tokens,
            },
        )

    def _update_cache(self, combined_metrics: Metrics) -> None:
        """Incrementally update cache by adding only new entries.

        Instead of rebuilding the entire cache each time, we only process new entries
        that have been added since the last update. This is more efficient for
        real-world execution where new LLM calls happen incrementally.

        Checks if new entries have been added and only updates if needed.
        """
        current_count = len(combined_metrics.response_latencies) + len(
            combined_metrics.token_usages
        )
        if current_count <= self._last_processed_count:
            return  # No new entries, skip update

        # Build latency lookup for new entries
        latency_map: dict[str, tuple[float, int]] = {}
        for i, response_latency in enumerate(combined_metrics.response_latencies):
            latency_map[response_latency.response_id] = (response_latency.latency, i)

        # Add new token_usages to cache
        for token_usage in combined_metrics.token_usages:
            response_id = token_usage.response_id
            if response_id not in self._cache:
                latency, latency_index = latency_map.get(response_id, (0.0, -1))
                self._cache[response_id] = (latency, latency_index, token_usage)

        self._last_processed_count = current_count


# ============================================================================
# Custom Visualizer
# ============================================================================
class TokenCountingVisualizer(ConversationVisualizerBase):
    """A visualizer that shows step counts, tool names, and detailed LLM metrics.

    This visualizer produces concise output showing:
    - LLM call completions with latency, cost and token information
    - Tool execution steps with command/path details
    - Error messages

    Example output:
       1.  LLM: 2.3s, tokens: 0150, cost $0.00
       2. Tool: file_editor:view /path/to/file.txt
       3.  LLM: 1.5s, tokens: 0300, cost $0.01
       4. Tool: file_editor:str_replace /path/to/file.txt
    """

    def __init__(self, name: str | None = None):
        """Initialize the token counting visualizer.

        Args:
            name: Optional name to identify the agent/conversation.
                                  Note: This visualizer doesn't use it in output,
                                  but accepts it for compatibility with the base class.
        """
        # Initialize parent - state will be set later via initialize()
        super().__init__(name=name)

        # Track state for minimal progress output
        self._event_counter = 0  # Sequential counter for all events
        self._displayed_response_ids: set[str] = set()  # Track displayed LLM calls
        self._metrics_cache = MetricsCache()  # Handles metrics caching and lookups

        # Register event handlers via decorators
        self._event_handlers: dict[type[Event], Callable[[Event], None]] = {}
        for attr_name in dir(self):
            attr = getattr(self, attr_name)
            if hasattr(attr, "_handles_event_type"):
                self._event_handlers[attr._handles_event_type] = attr

    def _get_metrics_for_response_id(
        self, response_id: str
    ) -> tuple[float, float, dict] | None:
        """Extract latency, cost, and token usage for a specific response_id.

        Returns:
            Tuple of (latency, cost, token_info_dict) or None if not found.
            token_info_dict contains: prompt_tokens, completion_tokens, total_tokens
        """
        if not self.conversation_stats:
            return None

        combined_metrics: Metrics = self.conversation_stats.get_combined_metrics()
        return self._metrics_cache.get_metrics(response_id, combined_metrics)

    def _format_llm_call_line(self, response_id: str) -> str | None:
        """Format LLM call line with cost and token information.

        Returns:
            Formatted string or None if already displayed.
        """
        if response_id in self._displayed_response_ids:
            return None

        self._displayed_response_ids.add(response_id)

        metrics_info = self._get_metrics_for_response_id(response_id)
        if metrics_info:
            latency, cost, token_info = metrics_info
            total_tokens = token_info["total_tokens"]
            return (
                f"{'LLM:':>5} {latency:.1f}s, tokens: {total_tokens:04d}, "
                f"cost ${cost:.2f}"
            )

        # Fallback if metrics not available
        return f"{'LLM:':>5} 0.0s, tokens: 0000, cost $0.00"

    def _format_tool_line(self, tool_name: str, action: Action) -> str:
        """Format a tool execution line with command and path details.

        Args:
            tool_name: Name of the tool being executed
            action: The Action object from the SDK
                (may have 'command' and/or 'path' attributes)

        Returns:
            Formatted tool line string
        """
        # Extract command/action details from the action object
        command_str = getattr(action, "command", "")
        path_str = getattr(action, "path", "")

        if command_str and path_str:
            return f"{'Tool:':>5} {tool_name}:{command_str} {path_str}"
        elif command_str:
            return f"{'Tool:':>5} {tool_name}:{command_str}"
        else:
            return f"{'Tool:':>5} {tool_name}"

    def on_event(self, event: Event) -> None:
        """Dispatch events to registered handlers."""
        handler = self._event_handlers.get(type(event))
        if handler:
            handler(event)

    @handles(ActionEvent)
    def _handle_action_event(self, event: ActionEvent) -> None:
        """Handle ActionEvent - track LLM calls and show tool execution."""
        # Show LLM call that generated this action event
        # In the SDK, a single LLM response can generate multiple ActionEvents
        # (parallel function calling). All ActionEvents from the same LLM response
        # share the same llm_response_id. We show the LLM call once per response_id
        # (deduplication handled by _format_llm_call_line), even if action is None
        # (non-executable tool calls still have an associated LLM call).
        if event.llm_response_id:
            llm_line = self._format_llm_call_line(event.llm_response_id)
            if llm_line:
                self._event_counter += 1
                print(f"{self._event_counter:>4}. {llm_line}", flush=True)

        # Skip tool execution if action is None (non-executable tool calls)
        # Example: Agent tries to call a tool that doesn't exist (e.g., "missing_tool")
        # The SDK creates an ActionEvent with action=None and then emits an
        # AgentErrorEvent
        if not event.action:
            return

        # Show tool execution
        self._event_counter += 1
        tool_name = event.tool_name or "unknown"

        tool_line = self._format_tool_line(tool_name, event.action)
        print(f"{self._event_counter:>4}. {tool_line}", flush=True)

    @handles(MessageEvent)
    def _handle_message_event(self, event: MessageEvent) -> None:
        """Handle MessageEvent - track LLM calls."""
        # Show LLM call for agent messages without tool calls
        if event.source == "agent" and event.llm_response_id:
            llm_line = self._format_llm_call_line(event.llm_response_id)
            if llm_line:
                self._event_counter += 1
                print(f"{self._event_counter:>4}. {llm_line}", flush=True)

    def _truncate_error(self, error_msg: str, max_length: int = 100) -> str:
        """Truncate error message if it exceeds max_length.

        Args:
            error_msg: The error message to truncate
            max_length: Maximum length before truncation

        Returns:
            Truncated error message with "..." suffix if needed
        """
        if len(error_msg) > max_length:
            return error_msg[:max_length] + "..."
        return error_msg

    @handles(AgentErrorEvent)
    def _handle_error_event(self, event: AgentErrorEvent) -> None:
        """Handle AgentErrorEvent - show errors."""
        self._event_counter += 1
        error_preview = self._truncate_error(event.error)
        print(f"{self._event_counter:>4}. {'Error:':>5} {error_preview}", flush=True)


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
    token_counting_visualizer = TokenCountingVisualizer()

    # Start a conversation with custom visualizer
    cwd = os.getcwd()
    conversation = Conversation(
        agent=agent,
        workspace=cwd,
        visualizer=token_counting_visualizer,
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
