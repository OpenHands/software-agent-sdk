"""Test the ConversationExecutionStatus enum functionality."""

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM


def test_agent_execution_state_enum_basic():
    """Test basic ConversationExecutionStatus enum functionality."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Test initial state
    assert conversation._state.execution_status == ConversationExecutionStatus.IDLE

    # Test setting enum directly
    conversation._state.execution_status = ConversationExecutionStatus.RUNNING
    assert conversation._state.execution_status == ConversationExecutionStatus.RUNNING

    # Test setting to FINISHED
    conversation._state.execution_status = ConversationExecutionStatus.FINISHED
    assert conversation._state.execution_status == ConversationExecutionStatus.FINISHED

    # Test setting to PAUSED
    conversation._state.execution_status = ConversationExecutionStatus.PAUSED
    assert conversation._state.execution_status == ConversationExecutionStatus.PAUSED

    # Test setting to WAITING_FOR_CONFIRMATION
    conversation._state.execution_status = (
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    assert (
        conversation._state.execution_status
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    # Test setting to ERROR
    conversation._state.execution_status = ConversationExecutionStatus.ERROR
    assert conversation._state.execution_status == ConversationExecutionStatus.ERROR


def test_enum_values():
    """Test that all enum values are correct."""
    assert ConversationExecutionStatus.IDLE == "idle"
    assert ConversationExecutionStatus.RUNNING == "running"
    assert ConversationExecutionStatus.PAUSED == "paused"
    assert (
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
        == "waiting_for_confirmation"
    )
    assert ConversationExecutionStatus.FINISHED == "finished"
    assert ConversationExecutionStatus.ERROR == "error"


def test_enum_serialization():
    """Test that the enum serializes and deserializes correctly."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Set to different states and test serialization
    conversation._state.execution_status = ConversationExecutionStatus.FINISHED
    serialized = conversation._state.model_dump_json()
    assert '"execution_status": "finished"' in serialized

    conversation._state.execution_status = ConversationExecutionStatus.PAUSED
    serialized = conversation._state.model_dump_json()
    assert '"execution_status": "paused"' in serialized

    conversation._state.execution_status = (
        ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    serialized = conversation._state.model_dump_json()
    assert '"execution_status": "waiting_for_confirmation"' in serialized

    conversation._state.execution_status = ConversationExecutionStatus.ERROR
    serialized = conversation._state.model_dump_json()
    assert '"execution_status": "error"' in serialized
