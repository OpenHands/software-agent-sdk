import os
import re

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from openhands.sdk.conversation.token_display import (
    compute_token_display,
    get_default_mode_from_env,
)
from openhands.sdk.conversation.visualizer.base import ConversationVisualizerBase
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    ConversationStateUpdateEvent,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation, CondensationRequest


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


class DefaultConversationVisualizer(ConversationVisualizerBase):
    """Handles visualization of conversation events with Rich formatting.

    Provides Rich-formatted output with panels and complete content display.
    """

    _console: Console
    _skip_user_messages: bool
    _highlight_patterns: dict[str, str]

    def __init__(
        self,
        highlight_regex: dict[str, str] | None = DEFAULT_HIGHLIGHT_REGEX,
        skip_user_messages: bool = False,
    ):
        """Initialize the visualizer.

        Args:
            highlight_regex: Dictionary mapping regex patterns to Rich color styles
                           for highlighting keywords in the visualizer.
                           For example: {"Reasoning:": "bold blue",
                           "Thought:": "bold green"}
            skip_user_messages: If True, skip displaying user messages. Useful for
                                scenarios where user input is not relevant to show.
        """
        super().__init__()
        self._console = Console()
        self._skip_user_messages = skip_user_messages
        self._highlight_patterns = highlight_regex or {}

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
            title = f"[bold {_SYSTEM_COLOR}]System Prompt[/bold {_SYSTEM_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_SYSTEM_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, ActionEvent):
            # Check if action is None (non-executable)
            if event.action is None:
                title = (
                    f"[bold {_ACTION_COLOR}]Agent Action (Not Executed)"
                    f"[/bold {_ACTION_COLOR}]"
                )
            else:
                title = f"[bold {_ACTION_COLOR}]Agent Action[/bold {_ACTION_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_ACTION_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, ObservationEvent):
            title = (
                f"[bold {_OBSERVATION_COLOR}]Observation[/bold {_OBSERVATION_COLOR}]"
            )
            return Panel(
                content,
                title=title,
                border_style=_OBSERVATION_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, UserRejectObservation):
            title = f"[bold {_ERROR_COLOR}]User Rejected Action[/bold {_ERROR_COLOR}]"
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

            # Simple titles for base visualizer
            if event.llm_message.role == "user":
                title_text = f"[bold {role_color}]Message from User[/bold {role_color}]"
            else:
                title_text = (
                    f"[bold {role_color}]Message from Agent[/bold {role_color}]"
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
            title = f"[bold {_ERROR_COLOR}]Agent Error[/bold {_ERROR_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_ERROR_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, PauseEvent):
            title = f"[bold {_PAUSE_COLOR}]User Paused[/bold {_PAUSE_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_PAUSE_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, Condensation):
            title = f"[bold {_SYSTEM_COLOR}]Condensation[/bold {_SYSTEM_COLOR}]"
            return Panel(
                content,
                title=title,
                subtitle=self._format_metrics_subtitle(),
                border_style=_SYSTEM_COLOR,
                expand=True,
            )

        elif isinstance(event, CondensationRequest):
            title = f"[bold {_SYSTEM_COLOR}]Condensation Request[/bold {_SYSTEM_COLOR}]"
            return Panel(
                content,
                title=title,
                border_style=_SYSTEM_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )
        elif isinstance(event, ConversationStateUpdateEvent):
            # Skip visualizing conversation state updates - these are internal events
            return None
        else:
            # Fallback panel for unknown event types
            title = (
                f"[bold {_ERROR_COLOR}]UNKNOWN Event: "
                f"{event.__class__.__name__}[/bold {_ERROR_COLOR}]"
            )
            return Panel(
                content,
                title=title,
                subtitle=f"({event.source})",
                border_style=_ERROR_COLOR,
                padding=_PANEL_PADDING,
                expand=True,
            )

    def _format_metrics_subtitle(self) -> str | None:
        """Format LLM metrics subtitle based on conversation stats.

        Uses TokenDisplay utility to compute values and supports env-configured
        mode (per-context vs accumulated) and optional since-last delta.
        """
        display_mode = get_default_mode_from_env()
        include_since_last = os.environ.get(
            "OH_TOKENS_VIEW_DELTA", "false"
        ).lower() in {"1", "true", "yes"}

        stats = self.conversation_stats
        if not stats:
            return None

        data = compute_token_display(
            stats=stats,
            mode=display_mode,
            include_since_last=include_since_last,
        )
        if not data:
            return None

        def abbr(n: int | float) -> str:
            n = int(n or 0)
            if n >= 1_000_000_000:
                val, suffix = n / 1_000_000_000, "B"
            elif n >= 1_000_000:
                val, suffix = n / 1_000_000, "M"
            elif n >= 1_000:
                val, suffix = n / 1_000, "K"
            else:
                return str(n)
            return f"{val:.2f}".rstrip("0").rstrip(".") + suffix

        cache_rate = (
            f"{(data.cache_hit_rate * 100):.2f}%"
            if data.cache_hit_rate is not None
            else "N/A"
        )
        cost_str = f"{data.total_cost:.4f}"

        parts: list[str] = []
        input_part = f"[cyan]↑ input {abbr(data.input_tokens)}"
        if include_since_last and data.since_last_input_tokens is not None:
            input_part += f" (+{abbr(data.since_last_input_tokens)})"
        input_part += "[/cyan]"
        parts.append(input_part)

        parts.append(f"[magenta]cache hit {cache_rate}[/magenta]")
        if data.reasoning_tokens > 0:
            parts.append(f"[yellow] reasoning {abbr(data.reasoning_tokens)}[/yellow]")
        parts.append(f"[blue]↓ output {abbr(data.output_tokens)}[/blue]")
        parts.append(f"[green]$ {cost_str} (total)[/green]")

        return "Tokens: " + " • ".join(parts)
