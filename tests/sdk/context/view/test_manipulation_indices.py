from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import (
    Message,
    TextContent,
)


def message_event(content: str) -> MessageEvent:
    """Helper to create a MessageEvent."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def test_complete_empty_list() -> None:
    """Test manipulation_indices with empty event list."""
    manipulation_indices = ManipulationIndices.complete([])
    assert list(manipulation_indices) == [0]


def test_complete_single_message_event() -> None:
    """Test manipulation_indices with a single message event."""
    manipulation_indices = ManipulationIndices.complete([message_event("Event 0")])
    assert list(manipulation_indices) == [0, 1]


def test_complete_multiple_message_events() -> None:
    """Test manipulation_indices with multiple message events."""
    manipulation_indices = ManipulationIndices.complete(
        [
            message_event("Event 0"),
            message_event("Event 1"),
            message_event("Event 2"),
        ]
    )
    assert list(manipulation_indices) == [0, 1, 2, 3]
