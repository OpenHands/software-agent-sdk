from unittest.mock import MagicMock

from openhands.sdk.context.condenser.no_op_condenser import NoOpCondenser
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


def message_event(content: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


def test_noop_condenser() -> None:
    """Test that NoOpCondensers preserve their input events."""
    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]

    condenser = NoOpCondenser()
    view = View.from_events(events)

    condensation_result = condenser.condense(view)
    assert isinstance(condensation_result, View)
    assert condensation_result.events == events


def test_noop_condenser_with_llm() -> None:
    """Test that NoOpCondenser works with optional agent_llm parameter."""
    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]

    condenser = NoOpCondenser()
    view = View.from_events(events)

    # Create a mock LLM
    mock_llm = MagicMock(spec=LLM)

    # Condense with agent_llm parameter
    condensation_result = condenser.condense(view, agent_llm=mock_llm)
    assert isinstance(condensation_result, View)
    assert condensation_result.events == events
