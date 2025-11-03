import re
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation


if TYPE_CHECKING:
    from openhands.sdk.conversation.conversation_stats import ConversationStats


# These are external inputs
_OBSERVATION_COLOR = "yellow"
_MESSAGE_USER_COLOR = "gold3"
_PAUSE_COLOR = "bright_yellow"
# These are internal system stuff
_SYSTEM_COLOR = "magenta"
_THOUGHT_COLOR = "bright_black"
_ERROR_COLOR = "red"
# These are agent actions
_ACTION_COLOR = "blue"
_MESSAGE_ASSISTANT_COLOR = _ACTION_COLOR

DEFAULT_HIGHLIGHT_REGEX = {
    r"^Reasoning:": f"bold {_THOUGHT_COLOR}",
    r"^Thought:": f"bold {_THOUGHT_COLOR}",
    r"^Action:": f"bold {_ACTION_COLOR}",
    r"^Arguments:": f"bold {_ACTION_COLOR}",
    r"^Tool:": f"bold {_OBSERVATION_COLOR}",
    r"^Result:": f"bold {_OBSERVATION_COLOR}",
    r"^Rejection Reason:": f"bold {_ERROR_COLOR}",
    # Markdown-style
    r"\*\*(.*?)\*\*": "bold",
    r"\*(.*?)\*": "italic",
}

_PANEL_PADDING = (1, 1)


class ConversationVisualizer:
    """Handles visualization of conversation events with Rich formatting.

    Provides Rich-formatted output with panels and complete content display.
    """

    _console: Console
    _skip_user_messages: bool
    _conversation_stats: "ConversationStats | None"
    _name_for_visualization: str | None

    def __init__(
        self,
        highlight_regex: dict[str, str] | None = None,
        skip_user_messages: bool = False,
        conversation_stats: "ConversationStats | None" = None,
        name_for_visualization: str | None = None,
    ):
        """Initialize the visualizer.

        Args:
            highlight_regex: Dictionary mapping regex patterns to Rich color styles
                           for highlighting keywords in the visualizer.
                           For example: {"Reasoning:": "bold blue",
                           "Thought:": "bold green"}
            skip_user_messages: If True, skip displaying user messages. Useful for
                                scenarios where user input is not relevant to show.
            conversation_stats: ConversationStats object to display metrics information.
            name_for_visualization: Optional name to prefix in panel titles to identify
                                  which agent/conversation is speaking.
        """
        self._console = Console()
        self._skip_user_messages = skip_user_messages
        self._highlight_patterns: dict[str, str] = highlight_regex or {}
        self._conversation_stats = conversation_stats
        self._name_for_visualization = (
            name_for_visualization.capitalize() if name_for_visualization else None
        )

    def on_event(self, event: Event) -> None:
        """Main event handler that displays events with Rich formatting."""
        panel = self._create_event_panel(event)
        if panel:
            self._console.print(panel)
            self._console.print()  # Add spacing between events

    def _apply_highlighting(self, text: Text) -> Text:
        """Apply regex-based highlighting to text content.

        Args:
            text: The Rich Text object to highlight

        Returns:
            A new Text object with highlighting applied
        """
        if not self._highlight_patterns:
            return text

        # Create a copy to avoid modifying the original
        highlighted = text.copy()

        # Apply each pattern using Rich's built-in highlight_regex method
        for pattern, style in self._highlight_patterns.items():
            pattern_compiled = re.compile(pattern, re.MULTILINE)
            highlighted.highlight_regex(pattern_compiled, style)

        return highlighted

    def _create_event_panel(self, event: Event) -> Panel | None:
        """Create a Rich Panel for the event with appropriate styling."""
        # Use the event's visualize property for content
        content = event.visualize

        if not content.plain.strip():
            return None

        # Apply highlighting if configured
        if self._highlight_patterns:
            content = self._apply_highlighting(content)

        # Determine panel styling based on event type
        if isinstance(event, SystemPromptEvent):
            title = f"[bold {_SYSTEM_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"System Prompt[/bold {_SYSTEM_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_SYSTEM_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, ActionEvent):
            # Check if action is None (non-executable)
            title = f"[bold {_ACTION_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            if event.action is None:
                title += f"Agent Action (Not Executed)[/bold {_ACTION_COLOR}]"
            else:
                title += f"Agent Action[/bold {_ACTION_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_ACTION_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, ObservationEvent):
            title = f"[bold {_OBSERVATION_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"Observation[/bold {_OBSERVATION_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_OBSERVATION_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, UserRejectObservation):
            title = f"[bold {_ERROR_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"User Rejected Action[/bold {_ERROR_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_ERROR_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, MessageEvent):
            if (
                self._skip_user_messages
                and event.llm_message
                and event.llm_message.role == "user"
            ):
                return
            assert event.llm_message is not None
            # Role-based styling
            role_colors = {
                "user": _MESSAGE_USER_COLOR,
                "assistant": _MESSAGE_ASSISTANT_COLOR,
            }
            role_color = role_colors.get(event.llm_message.role, "white")

            # "User Message To [Name] Agent" for user
            # "Message from [Name] Agent" for agent
            agent_name = (
                f"{self._name_for_visualization} "
                if self._name_for_visualization
                else ""
            )

            if event.llm_message.role == "user":
                title_text = (
                    f"[bold {role_color}]User Message to "
                    f"{agent_name}Agent[/bold {role_color}]"
                )
            else:
                title_text = (
                    f"[bold {role_color}]Message from "
                    f"{agent_name}Agent[/bold {role_color}]"
                )
            return Panel(
                content,
                title=title_text,
                subtitle=self._format_metrics_subtitle(),
                border_style=role_color,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, AgentErrorEvent):
            title = f"[bold {_ERROR_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"Agent Error[/bold {_ERROR_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_ERROR_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, PauseEvent):
            title = f"[bold {_PAUSE_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"User Paused[/bold {_PAUSE_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_PAUSE_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, Condensation):
            title = f"[bold {_SYSTEM_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"Condensation[/bold {_SYSTEM_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_SYSTEM_COLOR,
                expand=True,
            )
        else:
            # Fallback panel for unknown event types
            title = f"[bold {_ERROR_COLOR}]"
            if self._name_for_visualization:
                title += f"{self._name_for_visualization} "
            title += f"UNKNOWN Event: {event.__class__.__name__}[/bold {_ERROR_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=f"({event.source})",
                border_style=_ERROR_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )

    def _format_metrics_subtitle(self) -> str | None:
        """Format LLM metrics as a visually appealing subtitle string with icons,
        colors, and k/m abbreviations using conversation stats."""
        if not self._conversation_stats:
            return None

        combined_metrics = self._conversation_stats.get_combined_metrics()
        if not combined_metrics or not combined_metrics.accumulated_token_usage:
            return None

        usage = combined_metrics.accumulated_token_usage
        cost = combined_metrics.accumulated_cost or 0.0

        # helper: 1234 -> "1.2K", 1200000 -> "1.2M"
        def abbr(n: int | float) -> str:
            n = int(n or 0)
            if n >= 1_000_000_000:
                s = f"{n / 1_000_000_000:.2f}B"
            elif n >= 1_000_000:
                s = f"{n / 1_000_000:.2f}M"
            elif n >= 1_000:
                s = f"{n / 1_000:.2f}K"
            else:
                return str(n)
            return s.replace(".0", "")

        input_tokens = abbr(usage.prompt_tokens or 0)
        output_tokens = abbr(usage.completion_tokens or 0)

        # Cache hit rate (prompt + cache)
        prompt = usage.prompt_tokens or 0
        cache_read = usage.cache_read_tokens or 0
        cache_rate = f"{(cache_read / prompt * 100):.2f}%" if prompt > 0 else "N/A"
        reasoning_tokens = usage.reasoning_tokens or 0

        # Cost
        cost_str = f"{cost:.4f}" if cost > 0 else "0.00"

        # Build with fixed color scheme
        parts: list[str] = []
        parts.append(f"[cyan]↑ input {input_tokens}[/cyan]")
        parts.append(f"[magenta]cache hit {cache_rate}[/magenta]")
        if reasoning_tokens > 0:
            parts.append(f"[yellow] reasoning {abbr(reasoning_tokens)}[/yellow]")
        parts.append(f"[blue]↓ output {output_tokens}[/blue]")
        parts.append(f"[green]$ {cost_str}[/green]")

        return "Tokens: " + " • ".join(parts)


def create_default_visualizer(
    highlight_regex: dict[str, str] | None = None,
    conversation_stats: "ConversationStats | None" = None,
    name_for_visualization: str | None = None,
    **kwargs,
) -> ConversationVisualizer:
    """Create a default conversation visualizer instance.

    Args:
        highlight_regex: Dictionary mapping regex patterns to Rich color styles
                       for highlighting keywords in the visualizer.
                       For example: {"Reasoning:": "bold blue",
                       "Thought:": "bold green"}
        conversation_stats: ConversationStats object to display metrics information.
        name_for_visualization: Optional name to prefix in panel titles to identify
                              which agent/conversation is speaking.
    """
    return ConversationVisualizer(
        highlight_regex=DEFAULT_HIGHLIGHT_REGEX
        if highlight_regex is None
        else highlight_regex,
        conversation_stats=conversation_stats,
        name_for_visualization=name_for_visualization,
        **kwargs,
    )
