"""
Delegation-specific visualizer that shows sender/receiver information for
multi-agent delegation.
"""

from rich.panel import Panel

from openhands.sdk.conversation.visualizer.default import DefaultConversationVisualizer
from openhands.sdk.event import MessageEvent


def _format_agent_name(name: str) -> str:
    """
    Convert snake_case or camelCase agent name to Title Case for display.

    Args:
        name: Agent name in snake_case (e.g., "lodging_expert") or
              camelCase (e.g., "MainAgent") or already formatted (e.g., "Main Agent")

    Returns:
        Formatted name in Title Case (e.g., "Lodging Expert" or "Main Agent")

    Examples:
        >>> _format_agent_name("lodging_expert")
        'Lodging Expert'
        >>> _format_agent_name("MainAgent")
        'Main Agent'
        >>> _format_agent_name("main_delegator")
        'Main Delegator'
        >>> _format_agent_name("Main Agent")
        'Main Agent'
    """
    # If already has spaces, assume it's already formatted
    if " " in name:
        return name

    # Handle snake_case by replacing underscores with spaces
    if "_" in name:
        return name.replace("_", " ").title()

    # Handle camelCase/PascalCase by inserting spaces before capitals
    import re

    # Insert space before each capital letter (except the first one)
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
    return spaced.title()


class DelegationVisualizer(DefaultConversationVisualizer):
    """
    Custom visualizer for agent delegation that shows detailed sender/receiver
    information.

    This visualizer extends the default visualizer to provide clearer
    visualization of multi-agent conversations during delegation scenarios.
    It shows:
    - Who sent each message (e.g., "Delegator", "Lodging Expert")
    - Who the intended recipient is
    - Clear directional flow between agents

    Example titles:
    - "Delegator Message to Lodging Expert"
    - "Lodging Expert Message to Delegator"
    - "Message from User to Delegator"
    """

    _name: str | None

    def __init__(
        self,
        name: str | None = None,
        highlight_regex: dict[str, str] | None = None,
        skip_user_messages: bool = False,
    ):
        """Initialize the delegation visualizer.

        Args:
            name: Agent name to display in panel titles for delegation context.
            highlight_regex: Dictionary mapping regex patterns to Rich color styles
                           for highlighting keywords in the visualizer.
            skip_user_messages: If True, skip displaying user messages.
        """
        super().__init__(
            highlight_regex=highlight_regex,
            skip_user_messages=skip_user_messages,
        )
        self._name = name

    def _create_message_event_panel(self, event: MessageEvent) -> Panel | None:
        """
        Create a panel for a message event with delegation-specific
        sender/receiver info.

        For user messages:
        - If sender is set: "[Sender] Message to [Agent]"
        - Otherwise: "Message from User to [Agent]"

        For agent messages:
        - Derives recipient from event history (last user message sender)
        - If recipient found: "[Agent] Message to [Recipient]"
        - Otherwise: "Message from [Agent]"

        Args:
            event: The message event to visualize

        Returns:
            A Rich Panel with delegation-aware title, or None if visualization fails
        """
        content = event.visualize
        if not content.plain.strip():
            return None

        assert event.llm_message is not None

        # Determine role color based on message role
        if event.llm_message.role == "user":
            role_color = "gold3"
        elif event.llm_message.role == "assistant":
            role_color = "blue"
        else:
            role_color = "white"

        # Build title with sender/recipient information for delegation
        agent_name = _format_agent_name(self._name) if self._name else "Agent"

        if event.llm_message.role == "user":
            if event.sender:
                # Message from another agent (via delegation)
                sender_display = _format_agent_name(event.sender)
                title_text = (
                    f"[bold {role_color}]{sender_display} Message to "
                    f"{agent_name}[/bold {role_color}]"
                )
            else:
                # Regular user message
                title_text = (
                    f"[bold {role_color}]Message from User to "
                    f"{agent_name}[/bold {role_color}]"
                )
        else:
            # For agent messages, derive recipient from last user message
            recipient = None
            if self._state:
                for evt in reversed(self._state.events):
                    if isinstance(evt, MessageEvent) and evt.llm_message.role == "user":
                        recipient = evt.sender
                        break

            if recipient:
                # Agent responding to another agent
                recipient_display = _format_agent_name(recipient)
                title_text = (
                    f"[bold {role_color}]{agent_name} Message to "
                    f"{recipient_display}[/bold {role_color}]"
                )
            else:
                # Agent responding to user
                title_text = (
                    f"[bold {role_color}]Message from {agent_name}[/bold {role_color}]"
                )

        return Panel(
            content,
            title=title_text,
            subtitle=self._format_metrics_subtitle(),
            border_style=role_color,
            padding=(1, 2),
            expand=True,
        )
