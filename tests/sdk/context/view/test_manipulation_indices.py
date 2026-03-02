from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from tests.sdk.context.view.conftest import message_event  # noqa: F401


def test_complete_empty_list() -> None:
    """Test complete manipulation indices with empty event list."""
    manipulation_indices = ManipulationIndices.complete([])
    assert manipulation_indices == {0}


def test_complete_single_message_event() -> None:
    """Test complete manipulation indices with a single message event."""
    manipulation_indices = ManipulationIndices.complete([message_event("Event 0")])
    assert manipulation_indices == {0, 1}


def test_complete_multiple_message_events() -> None:
    """Test complete manipulation indices with multiple message events."""
    manipulation_indices = ManipulationIndices.complete(
        [
            message_event("Event 0"),
            message_event("Event 1"),
            message_event("Event 2"),
        ]
    )
    assert manipulation_indices == {0, 1, 2, 3}
