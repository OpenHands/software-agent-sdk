"""Test backwards compatibility for security_analyzer field migration from Agent to ConversationState."""  # noqa: E501

import json

from openhands.sdk.agent import Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer


def test_agent_deserialization_with_security_analyzer_field(mock_llm):
    """Test that agents with security_analyzer field can be deserialized without errors."""  # noqa: E501
    # Create an agent and serialize it with the old security_analyzer field
    agent = Agent(llm=mock_llm, tools=[])
    agent_dict = agent.model_dump()

    # Add the old security_analyzer field to simulate old serialized data
    agent_dict["security_analyzer"] = {
        "kind": "LLMSecurityAnalyzer",
    }

    # This should not raise an error even though security_analyzer is no longer a field
    deserialized_agent = AgentBase.model_validate(agent_dict)

    # Verify the agent was created successfully
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.llm.model == "gpt-4o"

    # Verify that security_analyzer is not present in the agent
    assert not hasattr(deserialized_agent, "security_analyzer")


def test_agent_deserialization_without_security_analyzer_field(mock_llm):
    """Test that agents without security_analyzer field still work normally."""
    # Create an agent normally
    agent = Agent(llm=mock_llm, tools=[])
    agent_dict = agent.model_dump()

    # This should work as before
    deserialized_agent = AgentBase.model_validate(agent_dict)

    # Verify the agent was created successfully
    assert isinstance(deserialized_agent, Agent)
    assert deserialized_agent.llm.model == "gpt-4o"


def test_conversation_state_has_security_analyzer_field(mock_conversation_state):
    """Test that ConversationState now has the security_analyzer field."""
    state = mock_conversation_state

    # Verify the field exists and defaults to None
    assert hasattr(state, "security_analyzer")
    assert state.security_analyzer is None


def test_conversation_state_security_analyzer_assignment(mock_conversation_state):
    """Test that we can assign a security analyzer to ConversationState."""
    state = mock_conversation_state

    # Create a security analyzer
    analyzer = LLMSecurityAnalyzer()

    # Assign it to the state
    state.security_analyzer = analyzer

    # Verify it was assigned correctly
    assert state.security_analyzer is not None
    assert isinstance(state.security_analyzer, LLMSecurityAnalyzer)


def test_update_security_analyzer_configuration_sets_state_field(
    mock_conversation_state,
):
    """Test that update_security_analyzer_configuration sets the state field."""
    state = mock_conversation_state

    # Create a security analyzer
    analyzer = LLMSecurityAnalyzer()

    # Update the configuration
    state.update_security_analyzer_and_record_transitions(analyzer)

    # Verify the state field was set
    assert state.security_analyzer is analyzer


def test_update_security_analyzer_configuration_with_none(mock_conversation_state):
    """Test that update_security_analyzer_configuration works with None."""
    state = mock_conversation_state

    # Set to None
    state.update_security_analyzer_and_record_transitions(None)

    # Verify the state field was set to None
    assert state.security_analyzer is None


def test_json_serialization_roundtrip(mock_conversation_state):
    """Test that ConversationState with security_analyzer can be serialized and deserialized."""  # noqa: E501
    state = mock_conversation_state

    # Create and assign a security analyzer
    analyzer = LLMSecurityAnalyzer()
    state.update_security_analyzer_and_record_transitions(analyzer)

    # Serialize to JSON
    json_data = state.model_dump_json()

    # Deserialize from JSON
    state_dict = json.loads(json_data)
    restored_state = type(state).model_validate(state_dict)

    # Verify the security analyzer was preserved
    assert restored_state.security_analyzer is not None
    assert isinstance(restored_state.security_analyzer, LLMSecurityAnalyzer)
