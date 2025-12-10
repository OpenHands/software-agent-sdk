"""
Utility functions for analyzing agent behavior in integration tests.

These functions help verify agent behavior patterns and adherence to system messages
by analyzing collected events from conversations.
"""

import fnmatch

from openhands.sdk.event.base import Event


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
    collected_events: list[Event], max_length: int = 50000
) -> str:
    """
    Get a summary of the conversation including agent thoughts and actions.

    Args:
        collected_events: List of events collected from conversation
        max_length: Maximum length of the summary

    Returns:
        String summary of the conversation
    """
    summary_parts = []
    from openhands.sdk.event.llm_convertible.system import SystemPromptEvent

    for event in collected_events:
        # Skip the (very long) system prompt so judges see actual agent behavior
        if isinstance(event, SystemPromptEvent):
            continue
        # Use the event's visualize property to get Rich Text representation
        visualized = event.visualize
        # Convert to plain text
        plain_text = visualized.plain.strip()
        if plain_text:
            # Add event type label and content
            event_type = event.__class__.__name__
            summary_parts.append(f"[{event_type}]\n{plain_text}\n")

    summary = "\n".join(summary_parts)
    if len(summary) > max_length:
        summary = summary[:max_length] + "..."
    return summary


def _matches_pattern(path: str, pattern: str) -> bool:
    """Helper to match file paths against patterns."""
    return fnmatch.fnmatch(path, pattern) or pattern in path
