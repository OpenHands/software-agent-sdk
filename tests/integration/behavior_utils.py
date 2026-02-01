"""
Utility functions for analyzing agent behavior in integration tests.

These functions help verify agent behavior patterns and adherence to system messages
by analyzing collected events from conversations.
"""

import fnmatch

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible.observation import (
    AgentErrorEvent,
    ObservationEvent,
)
from openhands.sdk.event.llm_convertible.system import SystemPromptEvent
from openhands.sdk.utils import maybe_truncate


def find_tool_calls(collected_events: list[Event], tool_name: str) -> list[Event]:
    """
    Find all ActionEvents where a specific tool was called.

    Args:
        collected_events: List of events collected from conversation
        tool_name: Name of the tool to search for
            (e.g., "file_editor", "terminal")

    Returns:
        List of ActionEvents matching the tool name
    """
    from openhands.sdk.event import ActionEvent

    return [
        event
        for event in collected_events
        if isinstance(event, ActionEvent) and event.tool_name == tool_name
    ]


def find_file_editing_operations(collected_events: list[Event]) -> list[Event]:
    """
    Find all file editing operations (create, str_replace, insert, undo_edit).

    Excludes read-only operations like 'view'.

    Args:
        collected_events: List of events collected from conversation

    Returns:
        List of ActionEvents that performed file editing
    """
    from openhands.sdk.event import ActionEvent
    from openhands.tools.file_editor.definition import FileEditorAction, FileEditorTool

    editing_operations = []
    for event in collected_events:
        if isinstance(event, ActionEvent) and event.tool_name == FileEditorTool.name:
            if event.action is not None:
                assert isinstance(event.action, FileEditorAction)
                # Check if the command is an editing operation
                if event.action.command in [
                    "create",
                    "str_replace",
                    "insert",
                    "undo_edit",
                ]:
                    editing_operations.append(event)
    return editing_operations


def find_file_operations(
    collected_events: list[Event], file_pattern: str | None = None
) -> list[Event]:
    """
    Find all file operations (both read and write).

    Args:
        collected_events: List of events collected from conversation
        file_pattern: Optional pattern to match against file paths
            (e.g., "*.md", "README")

    Returns:
        List of ActionEvents that performed file operations
    """
    from openhands.sdk.event import ActionEvent
    from openhands.tools.file_editor.definition import FileEditorAction, FileEditorTool

    file_operations = []
    for event in collected_events:
        if isinstance(event, ActionEvent) and event.tool_name == FileEditorTool.name:
            if event.action is not None:
                assert isinstance(event.action, FileEditorAction)
                if file_pattern is None or _matches_pattern(
                    event.action.path, file_pattern
                ):
                    file_operations.append(event)
    return file_operations


def check_bash_command_used(
    collected_events: list[Event], command_pattern: str
) -> list[Event]:
    """
    Check if agent used bash commands instead of specialized tools.

    Args:
        collected_events: List of events collected from conversation
        command_pattern: Pattern to search for in bash commands (e.g., "cat", "sed")

    Returns:
        List of ActionEvents where bash was used with the pattern
    """
    from openhands.sdk.event import ActionEvent
    from openhands.tools.terminal.definition import TerminalAction, TerminalTool

    bash_commands = []
    for event in collected_events:
        if isinstance(event, ActionEvent) and event.tool_name == TerminalTool.name:
            if event.action is not None:
                assert isinstance(event.action, TerminalAction)
                if command_pattern in event.action.command:
                    bash_commands.append(event)
    return bash_commands


def get_conversation_summary(
    collected_events: list[Event], max_observation_chars: int = 2000
) -> str:
    """
    Get a summary of the conversation including agent thoughts and actions.

    To prevent context window overflow in LLM judges, large observations are
    truncated to preserve both the beginning and end of the output.

    Args:
        collected_events: List of events collected from conversation
        max_observation_chars: Maximum characters for observation events.
            Uses head + tail truncation (default: 2000 = ~1000 head + ~1000 tail)

    Returns:
        String summary of the conversation
    """
    summary_parts = []

    # Custom truncation notice for judge context (simpler than default)
    judge_truncate_notice = (
        "\n... [Output truncated for brevity - showing head and tail] ...\n"
    )

    for event in collected_events:
        # Skip the (very long) system prompt so judges see actual agent behavior
        if isinstance(event, SystemPromptEvent):
            continue

        # Use the event's visualize property to get Rich Text representation
        visualized = event.visualize
        # Convert to plain text
        plain_text = visualized.plain.strip()

        if plain_text:
            # Truncate large observations to prevent context overflow
            # Keep error events in full as they're usually small and critical
            if isinstance(event, ObservationEvent) and not isinstance(
                event, AgentErrorEvent
            ):
                plain_text = maybe_truncate(
                    plain_text,
                    truncate_after=max_observation_chars,
                    truncate_notice=judge_truncate_notice,
                )

            # Add event type label and content
            event_type = event.__class__.__name__
            summary_parts.append(f"[{event_type}]\n{plain_text}\n")

    return "\n".join(summary_parts)


def _matches_pattern(path: str, pattern: str) -> bool:
    """Helper to match file paths against patterns."""
    return fnmatch.fnmatch(path, pattern) or pattern in path


def verify_all_actions_have_summary(collected_events: list[Event]) -> tuple[bool, str]:
    """
    Verify that all ActionEvents have a non-empty summary field.

    The summary field is always added to tool schemas and should be populated
    either by the LLM or with a default value.

    Args:
        collected_events: List of events collected from conversation

    Returns:
        Tuple of (success, reason) where success is True if all actions have
        summaries, and reason explains any failures
    """
    from openhands.sdk.event import ActionEvent

    action_events = [e for e in collected_events if isinstance(e, ActionEvent)]

    if not action_events:
        return True, "No action events found"

    missing_summaries = []
    for i, event in enumerate(action_events):
        if not event.summary or not event.summary.strip():
            missing_summaries.append(f"Action {i + 1}: {event.tool_name}")

    if missing_summaries:
        return False, f"Actions missing summaries: {', '.join(missing_summaries)}"

    return True, f"All {len(action_events)} actions have summaries"
