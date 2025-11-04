"""Test the AgentExecutionStatus enum functionality."""

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.state import AgentExecutionStatus
from openhands.sdk.llm import LLM


def test_agent_execution_state_enum_basic():
    """Test basic AgentExecutionStatus enum functionality."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Test initial state
    assert conversation._state.agent_status == AgentExecutionStatus.IDLE

    # Test setting enum directly
    conversation._state.agent_status = AgentExecutionStatus.RUNNING
    assert conversation._state.agent_status == AgentExecutionStatus.RUNNING

    # Test setting to FINISHED
    conversation._state.agent_status = AgentExecutionStatus.FINISHED
    assert conversation._state.agent_status == AgentExecutionStatus.FINISHED

    # Test setting to PAUSED
    conversation._state.agent_status = AgentExecutionStatus.PAUSED
    assert conversation._state.agent_status == AgentExecutionStatus.PAUSED

    # Test setting to WAITING_FOR_CONFIRMATION
    conversation._state.agent_status = AgentExecutionStatus.WAITING_FOR_CONFIRMATION
    assert (
        conversation._state.agent_status
        == AgentExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    # Test setting to ERROR
    conversation._state.agent_status = AgentExecutionStatus.ERROR
    assert conversation._state.agent_status == AgentExecutionStatus.ERROR


def test_enum_values():
    """Test that all enum values are correct."""
    assert AgentExecutionStatus.IDLE == "idle"
    assert AgentExecutionStatus.RUNNING == "running"
    assert AgentExecutionStatus.PAUSED == "paused"
    assert AgentExecutionStatus.WAITING_FOR_CONFIRMATION == "waiting_for_confirmation"
    assert AgentExecutionStatus.FINISHED == "finished"
    assert AgentExecutionStatus.ERROR == "error"


def test_enum_serialization():
    """Test that the enum serializes and deserializes correctly."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    conversation = Conversation(agent=agent)

    # Set to different states and test serialization
    conversation._state.agent_status = AgentExecutionStatus.FINISHED
    serialized = conversation._state.model_dump_json()
    assert '"agent_status": "finished"' in serialized

    conversation._state.agent_status = AgentExecutionStatus.PAUSED
    serialized = conversation._state.model_dump_json()
    assert '"agent_status": "paused"' in serialized

    conversation._state.agent_status = AgentExecutionStatus.WAITING_FOR_CONFIRMATION
    serialized = conversation._state.model_dump_json()
    assert '"agent_status": "waiting_for_confirmation"' in serialized

    conversation._state.agent_status = AgentExecutionStatus.ERROR
    serialized = conversation._state.model_dump_json()
    assert '"agent_status": "error"' in serialized
