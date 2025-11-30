"""
Delegation-specific visualizer that shows sender/receiver information for
multi-agent delegation.
"""

from rich.console import Group

from openhands.sdk.conversation.visualizer.default import (
    _ACTION_COLOR,
    _OBSERVATION_COLOR,
    _SYSTEM_COLOR,
    DefaultConversationVisualizer,
    build_event_block,
)
from openhands.sdk.event import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
)
from openhands.sdk.event.base import Event


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

    @staticmethod
    def _format_agent_name(name: str) -> str:
        """
        Convert snake_case or camelCase agent name to Title Case for display.

        Args:
            name: Agent name in snake_case (e.g., "lodging_expert") or
                  camelCase (e.g., "MainAgent") or already formatted
                  (e.g., "Main Agent")

        Returns:
            Formatted name in Title Case (e.g., "Lodging Expert" or "Main Agent")

        Examples:
            >>> DelegationVisualizer._format_agent_name("lodging_expert")
            'Lodging Expert'
            >>> DelegationVisualizer._format_agent_name("MainAgent")
            'Main Agent'
            >>> DelegationVisualizer._format_agent_name("main_delegator")
            'Main Delegator'
            >>> DelegationVisualizer._format_agent_name("Main Agent")
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

    def _create_event_block(self, event: Event) -> Group | None:
        """
        Override event block creation to add agent names to titles.

        For system prompts, actions, and observations, prepend the agent name
        (e.g., "Delegator Agent System Prompt", "Delegator Agent Action",
        "Lodging Expert Agent Observation").
        For messages, delegate to the specialized message handler.

        Args:
            event: The event to visualize

        Returns:
            A Rich Group with agent-specific title, or None if visualization fails
        """
        # For message events, use our specialized handler
        if isinstance(event, MessageEvent):
            return self._create_message_event_block(event)

        # For system prompts, actions, and observations, add agent name to the title
        if isinstance(event, (SystemPromptEvent, ActionEvent, ObservationEvent)):
            content = event.visualize
            if not content.plain.strip():
                return None

            # Apply highlighting if configured
            if self._highlight_patterns:
                content = self._apply_highlighting(content)

            agent_name = self._format_agent_name(self._name) if self._name else "Agent"

            if isinstance(event, SystemPromptEvent):
                title = f"{agent_name} Agent System Prompt"
                return build_event_block(
                    content=content,
                    title=title,
                    title_color=_SYSTEM_COLOR,
                )
            elif isinstance(event, ActionEvent):
                # Check if action is None (non-executable)
                if event.action is None:
                    title = f"{agent_name} Agent Action (Not Executed)"
                else:
                    title = f"{agent_name} Agent Action"
                return build_event_block(
                    content=content,
                    title=title,
                    title_color=_ACTION_COLOR,
                    subtitle=self._format_metrics_subtitle(),
                )
            else:  # ObservationEvent
                title = f"{agent_name} Agent Observation"
                return build_event_block(
                    content=content,
                    title=title,
                    title_color=_OBSERVATION_COLOR,
                )

        # For all other event types, use the parent implementation
        return super()._create_event_block(event)

    def _create_message_event_block(self, event: MessageEvent) -> Group | None:
        """
        Create a block for a message event with delegation-specific
        sender/receiver info.

        For user messages:
        - If sender is set: "[Sender] Agent Message to [Agent] Agent"
        - Otherwise: "User Message to [Agent] Agent"

        For agent messages:
        - Derives recipient from event history (last user message sender)
        - If recipient found: "[Agent] Agent Message to [Recipient] Agent"
        - Otherwise: "Message from [Agent] Agent to User"

        Args:
            event: The message event to visualize

        Returns:
            A Rich Group with delegation-aware title, or None if visualization fails
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
        agent_name = self._format_agent_name(self._name) if self._name else "Agent"

        if event.llm_message.role == "user":
            if event.sender:
                # Message from another agent (via delegation)
                sender_display = self._format_agent_name(event.sender)
                title = f"{sender_display} Agent Message to {agent_name} Agent"
            else:
                # Regular user message
                title = f"User Message to {agent_name} Agent"
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
                recipient_display = self._format_agent_name(recipient)
                title = f"{agent_name} Agent Message to {recipient_display} Agent"
            else:
                # Agent responding to user
                title = f"Message from {agent_name} Agent to User"

        return build_event_block(
            content=content,
            title=title,
            title_color=role_color,
            subtitle=self._format_metrics_subtitle(),
        )
