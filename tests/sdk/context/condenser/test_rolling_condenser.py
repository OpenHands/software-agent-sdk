from unittest.mock import MagicMock

import pytest

from openhands.sdk.context.condenser.base import (
    NoCondensationAvailableException,
    RollingCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.event.base import Event
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


def message_event(content: str) -> MessageEvent:
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


class MockRollingCondenser(RollingCondenser):
    """Mock implementation of RollingCondenser for testing."""

    def __init__(
        self,
        should_condense_value: bool = True,
        raise_exception: bool = False,
    ):
        self._should_condense_value = should_condense_value
        self._raise_exception = raise_exception

    def should_condense(self, view: View, agent_llm: LLM | None = None) -> bool:
        return self._should_condense_value

    def get_condensation(
        self, view: View, agent_llm: LLM | None = None
    ) -> Condensation:
        if self._raise_exception:
            raise NoCondensationAvailableException(
                "No condensation available due to API constraints"
            )
        # Return a simple condensation for successful case
        return Condensation(
            forgotten_event_ids=[view.events[0].id],
            summary="Mock summary",
            summary_offset=0,
            llm_response_id="mock-response-id",
        )


def test_rolling_condenser_returns_view_when_no_condensation_needed() -> None:
    """Test that RollingCondenser returns the original view when should_condense returns False."""
    condenser = MockRollingCondenser(should_condense_value=False)

    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]
    view = View.from_events(events)

    result = condenser.condense(view)

    assert isinstance(result, View)
    assert result == view


def test_rolling_condenser_returns_condensation_when_needed() -> None:
    """Test that RollingCondenser returns a Condensation when should_condense returns True."""
    condenser = MockRollingCondenser(should_condense_value=True, raise_exception=False)

    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]
    view = View.from_events(events)

    result = condenser.condense(view)

    assert isinstance(result, Condensation)
    assert result.summary == "Mock summary"


def test_rolling_condenser_returns_view_on_no_condensation_available_exception() -> (
    None
):
    """Test that RollingCondenser returns the original view when
    NoCondensationAvailableException is raised.

    This tests the exception handling added in base.py:105-110 which catches
    NoCondensationAvailableException from get_condensation() and returns the
    original view as a fallback.
    """
    condenser = MockRollingCondenser(should_condense_value=True, raise_exception=True)

    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]
    view = View.from_events(events)

    # Even though should_condense returns True, the exception should be caught
    # and the original view should be returned
    result = condenser.condense(view)

    assert isinstance(result, View)
    assert result == view
    assert result.events == events


def test_rolling_condenser_with_agent_llm() -> None:
    """Test that RollingCondenser works with optional agent_llm parameter."""
    condenser = MockRollingCondenser(should_condense_value=True, raise_exception=False)

    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]
    view = View.from_events(events)

    # Create a mock LLM
    mock_llm = MagicMock(spec=LLM)

    # Condense with agent_llm parameter
    result = condenser.condense(view, agent_llm=mock_llm)

    assert isinstance(result, Condensation)
    assert result.summary == "Mock summary"


def test_no_condensation_available_exception_message() -> None:
    """Test that NoCondensationAvailableException raisable with custom message."""
    exception_message = "Custom error message about API constraints"

    with pytest.raises(NoCondensationAvailableException, match=exception_message):
        raise NoCondensationAvailableException(exception_message)
