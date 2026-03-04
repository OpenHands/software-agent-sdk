"""Fixtures for API compliance tests."""

import uuid

import pytest

from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.types import ToolCallID
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool import Observation


class SimpleObservation(Observation):
    """Simple observation for testing."""

    result: str

    @property
    def to_llm_content(self) -> list[TextContent]:
        return [TextContent(text=self.result)]


def make_action_event(
    tool_call_id: ToolCallID | None = None,
    tool_name: str = "terminal",
) -> ActionEvent:
    """Create an ActionEvent for testing."""
    call_id = tool_call_id or f"call_{uuid.uuid4().hex[:8]}"
    event_id = str(uuid.uuid4())
    return ActionEvent(
        id=event_id,
        source="agent",
        thought=[TextContent(text="Let me do this")],
        tool_name=tool_name,
        tool_call_id=call_id,
        tool_call=MessageToolCall(
            id=call_id,
            name=tool_name,
            arguments='{"command": "ls"}',
            origin="completion",
        ),
        llm_response_id=str(uuid.uuid4()),
    )


def make_observation_event(
    action_event: ActionEvent,
    result: str = "output",
) -> ObservationEvent:
    """Create an ObservationEvent matching an ActionEvent."""
    return ObservationEvent(
        id=str(uuid.uuid4()),
        source="environment",
        tool_name=action_event.tool_name,
        tool_call_id=action_event.tool_call_id,
        action_id=action_event.id,
        observation=SimpleObservation(result=result),
    )


def make_orphan_observation_event(
    tool_call_id: ToolCallID,
    tool_name: str = "terminal",
    action_id: str | None = None,
) -> ObservationEvent:
    """Create an ObservationEvent with no matching action."""
    return ObservationEvent(
        id=str(uuid.uuid4()),
        source="environment",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        action_id=action_id or str(uuid.uuid4()),
        observation=SimpleObservation(result="orphan result"),
    )


def make_user_message_event(text: str = "Hello") -> MessageEvent:
    """Create a user MessageEvent."""
    return MessageEvent(
        id=str(uuid.uuid4()),
        source="user",
        llm_message=Message(
            role="user",
            content=[TextContent(text=text)],
        ),
    )


def make_assistant_message_event(text: str = "I'll help") -> MessageEvent:
    """Create an assistant MessageEvent."""
    return MessageEvent(
        id=str(uuid.uuid4()),
        source="agent",
        llm_message=Message(
            role="assistant",
            content=[TextContent(text=text)],
        ),
    )


@pytest.fixture
def action_event():
    """A single action event."""
    return make_action_event()


@pytest.fixture
def user_message_event():
    """A user message event."""
    return make_user_message_event()


@pytest.fixture
def assistant_message_event():
    """An assistant message event."""
    return make_assistant_message_event()
