from unittest.mock import MagicMock

import pytest

from openhands.sdk.context.condenser.base import (
    CondensationRequirement,
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
        condensation_requirement_value: CondensationRequirement | None = None,
        raise_exception: bool = False,
    ):
        self._condensation_requirement_value = condensation_requirement_value
        self._raise_exception = raise_exception

    def condensation_requirement(
        self, view: View, agent_llm: LLM | None = None
    ) -> CondensationRequirement | None:
        return self._condensation_requirement_value

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
    """Test that RollingCondenser returns the original view when
    condensation_requirement returns None.
    """
    condenser = MockRollingCondenser(condensation_requirement_value=None)

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
    """Test that RollingCondenser returns a Condensation when condensation_requirement
    returns HARD.
    """
    condenser = MockRollingCondenser(
        condensation_requirement_value=CondensationRequirement.HARD,
        raise_exception=False,
    )

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
    NoCondensationAvailableException is raised with SOFT requirement.

    This tests the exception handling for SOFT condensation requirements which catches
    NoCondensationAvailableException from get_condensation() and returns the
    original view as a fallback.
    """
    condenser = MockRollingCondenser(
        condensation_requirement_value=CondensationRequirement.SOFT,
        raise_exception=True,
    )

    events: list[Event] = [
        message_event("Event 1"),
        message_event("Event 2"),
        message_event("Event 3"),
    ]
    view = View.from_events(events)

    # Even though condensation_requirement returns SOFT, the exception should be
    # caught and the original view should be returned
    result = condenser.condense(view)

    assert isinstance(result, View)
    assert result == view
    assert result.events == events


def test_rolling_condenser_with_agent_llm() -> None:
    """Test that RollingCondenser works with optional agent_llm parameter."""
    condenser = MockRollingCondenser(
        condensation_requirement_value=CondensationRequirement.HARD,
        raise_exception=False,
    )

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
