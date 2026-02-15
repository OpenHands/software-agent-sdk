"""Tests for event immutability."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

import pytest

from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Condensation,
    CondensationRequest,
    Event,
    MessageEvent,
    ObservationEvent,
    PauseEvent,
    SystemPromptEvent,
    UserRejectObservation,
)
from openhands.sdk.llm import (
    ImageContent,
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.tool import ToolDefinition, ToolExecutor
from openhands.sdk.tool.schema import Action, Observation


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


class EventsImmutabilityMockAction(Action):
    """Mock action for testing."""

    command: str = "test_command"


class EventsImmutabilityMockObservation(Observation):
    """Mock observation for testing."""

    result: str = "test_result"

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=self.result)]


class EventsImmutabilityMockExecutor(ToolExecutor):
    """Mock executor for testing."""

    def __call__(
        self,
        action: EventsImmutabilityMockAction,
        conversation: "LocalConversation | None" = None,
    ) -> EventsImmutabilityMockObservation:
        return EventsImmutabilityMockObservation.from_text("test")


class EventsImmutabilityMockTool(
    ToolDefinition[EventsImmutabilityMockAction, EventsImmutabilityMockObservation]
):
    """Mock tool for testing."""

    @classmethod
    def create(cls, *args, **kwargs) -> Sequence[Self]:
        return [
            cls(
                description="Test tool",
                action_type=EventsImmutabilityMockAction,
                observation_type=EventsImmutabilityMockObservation,
                executor=EventsImmutabilityMockExecutor(),
            )
        ]


class _TestEventForImmutability(Event):
    """Test event class for immutability tests.

    This class is defined at module level (rather than inside a test function) to
    ensure it's importable by Pydantic during serialization/deserialization.
    Defining it inside a test function causes test pollution when running tests
    in parallel with pytest-xdist.
    """

    test_field: str = "test_value"


def test_event_base_is_frozen():
    """Test that Event instances are frozen and cannot be modified."""
    event = _TestEventForImmutability(source="agent", test_field="initial_value")

    # Test that we cannot modify any field
    with pytest.raises(Exception):  # Pydantic raises ValidationError for frozen models
        event.id = "modified_id"

    with pytest.raises(Exception):
        event.timestamp = "modified_timestamp"

    with pytest.raises(Exception):
        event.source = "user"

    with pytest.raises(Exception):
        event.test_field = "modified_value"


def test_system_prompt_event_is_frozen():
    """Test that SystemPromptEvent instances are frozen."""
    tool = EventsImmutabilityMockTool.create()[0]

    event = SystemPromptEvent(
        system_prompt=TextContent(text="Test system prompt"),
        tools=[tool],
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.system_prompt = TextContent(text="Modified prompt")

    with pytest.raises(Exception):
        event.tools = []

    with pytest.raises(Exception):
        event.id = "modified_id"


def test_action_event_is_frozen():
    """Test that ActionEvent instances are frozen."""
    action = EventsImmutabilityMockAction()
    tool_call = MessageToolCall(
        id="test_call_id", name="test_tool", arguments="{}", origin="completion"
    )

    event = ActionEvent(
        thought=[TextContent(text="Test thought")],
        action=action,
        tool_name="test_tool",
        tool_call_id="test_call_id",
        tool_call=tool_call,
        llm_response_id="test_response_id",
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.thought = [TextContent(text="Modified thought")]

    with pytest.raises(Exception):
        event.action = EventsImmutabilityMockAction(command="modified_command")

    with pytest.raises(Exception):
        event.tool_name = "modified_tool"

    with pytest.raises(Exception):
        event.reasoning_content = "modified_reasoning"


def test_observation_event_is_frozen():
    """Test that ObservationEvent instances are frozen."""
    observation = EventsImmutabilityMockObservation()

    event = ObservationEvent(
        observation=observation,
        action_id="test_action_id",
        tool_name="test_tool",
        tool_call_id="test_call_id",
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.observation = EventsImmutabilityMockObservation(result="modified_result")

    with pytest.raises(Exception):
        event.action_id = "modified_action_id"

    with pytest.raises(Exception):
        event.tool_name = "modified_tool"

    with pytest.raises(Exception):
        event.tool_call_id = "modified_call_id"


def test_message_event_is_frozen():
    """Test that MessageEvent instances are frozen."""
    message = Message(role="user", content=[TextContent(text="Test message")])

    event = MessageEvent(source="user", llm_message=message)

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.source = "agent"

    with pytest.raises(Exception):
        event.llm_message = Message(
            role="assistant", content=[TextContent(text="Modified message")]
        )

    with pytest.raises(Exception):
        event.activated_skills = ["test_skill"]

    with pytest.raises(Exception):
        event.extended_content = [TextContent(text="Extended content")]


def test_user_reject_observation_is_frozen():
    """Test that UserRejectObservation instances are frozen."""
    event = UserRejectObservation(
        action_id="test_action_id",
        tool_name="test_tool",
        tool_call_id="test_call_id",
        rejection_reason="Test rejection",
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.action_id = "modified_action_id"

    with pytest.raises(Exception):
        event.tool_name = "modified_tool"

    with pytest.raises(Exception):
        event.tool_call_id = "modified_call_id"

    with pytest.raises(Exception):
        event.rejection_reason = "Modified rejection"

    with pytest.raises(Exception):
        event.rejection_source = "hook"


def test_user_reject_observation_rejection_source():
    """Test that UserRejectObservation rejection_source field works correctly."""
    # Default should be "user"
    user_event = UserRejectObservation(
        action_id="test_action_id",
        tool_name="test_tool",
        tool_call_id="test_call_id",
        rejection_reason="User rejected",
    )
    assert user_event.rejection_source == "user"

    # Hook rejection should have "hook" source
    hook_event = UserRejectObservation(
        action_id="test_action_id",
        tool_name="test_tool",
        tool_call_id="test_call_id",
        rejection_reason="Blocked by hook",
        rejection_source="hook",
    )
    assert hook_event.rejection_source == "hook"


def test_agent_error_event_is_frozen():
    """Test that AgentErrorEvent instances are frozen."""
    event = AgentErrorEvent(
        error="Test error message", tool_call_id="test_call_id", tool_name="test_tool"
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.error = "Modified error message"

    with pytest.raises(Exception):
        event.source = "user"


def test_pause_event_is_frozen():
    """Test that PauseEvent instances are frozen."""
    event = PauseEvent()

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.source = "agent"

    with pytest.raises(Exception):
        event.id = "modified_id"


def test_condensation_is_frozen():
    """Test that Condensation instances are frozen."""
    event = Condensation(
        forgotten_event_ids=["event1", "event2"],
        summary="Test summary",
        llm_response_id="condensation_response_1",
    )

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.forgotten_event_ids = ["modified_event"]

    with pytest.raises(Exception):
        event.summary = "Modified summary"

    with pytest.raises(Exception):
        event.summary_offset = 10


def test_condensation_request_is_frozen():
    """Test that CondensationRequest instances are frozen."""
    event = CondensationRequest()

    # Test that we cannot modify any field
    with pytest.raises(Exception):
        event.source = "agent"

    with pytest.raises(Exception):
        event.id = "modified_id"


def test_event_model_copy_creates_new_instance():
    """Test that model_copy can create modified versions of frozen events."""
    event = PauseEvent()
    original_id = event.id

    # Create a copy with modified fields
    modified_event = event.model_copy(update={"id": "new_id"})

    # Verify that a new instance was created with modifications
    assert modified_event is not event
    assert event.id == original_id
    assert modified_event.id == "new_id"
    assert modified_event.source == event.source


def test_event_immutability_prevents_mutation_bugs():
    """Test that frozen events prevent the type of mutation bugs fixed in PR #226."""
    tool = EventsImmutabilityMockTool.create()[0]

    event = SystemPromptEvent(
        system_prompt=TextContent(text="Test system prompt"),
        tools=[tool],
    )

    # Store original tool data
    original_tool_name = event.tools[0].name
    original_tool_description = event.tools[0].description

    # Call visualize multiple times (this used to cause mutations)
    for _ in range(3):
        _ = event.visualize

    # Verify no mutation occurred - the event data should be unchanged
    assert event.tools[0].name == original_tool_name
    assert event.tools[0].description == original_tool_description

    # Verify that attempting to modify the event fields directly fails
    with pytest.raises(Exception):
        event.tools = []  # This should fail because the event is frozen
