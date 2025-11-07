"""Tests for security analyzer configuration tracking in ConversationState.

This module tests that security analyzer configuration is properly tracked
in ConversationState fields during conversation initialization and reinitialization.
"""

import tempfile
from datetime import datetime

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.llm import LLM
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer


@pytest.fixture
def mock_llm():
    """Create a mock LLM for testing."""
    return LLM(
        usage_id="test-llm",
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://test",
    )


@pytest.fixture
def agent_with_llm_analyzer(mock_llm):
    """Create an agent with LLMSecurityAnalyzer."""
    return Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())


@pytest.fixture
def agent_without_analyzer(mock_llm):
    """Create an agent without security analyzer."""
    return Agent(llm=mock_llm)


@pytest.mark.parametrize(
    "agent_fixture,expected_analyzer_type",
    [
        ("agent_with_llm_analyzer", "LLMSecurityAnalyzer"),
        ("agent_without_analyzer", None),
    ],
)
def test_new_conversation_sets_security_analyzer_state(
    request, agent_fixture, expected_analyzer_type
):
    """Test that new conversations set security analyzer configuration in ConversationState."""  # noqa: E501
    # Get the agent fixture
    agent = request.getfixturevalue(agent_fixture)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Check that we have the expected events
        events = conversation.state.events

        # Find SystemPromptEvent
        system_prompt_events = [e for e in events if isinstance(e, SystemPromptEvent)]
        assert len(system_prompt_events) == 1, (
            "Should have exactly one SystemPromptEvent"
        )

        # Verify the ConversationState has the correct security analyzer configuration
        assert len(conversation.state.security_analyzer_history) == 1
        assert (
            conversation.state.security_analyzer_history[0].analyzer_type
            == expected_analyzer_type
        )
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == expected_analyzer_type
        )
        assert isinstance(
            conversation.state.security_analyzer_history[0].timestamp, datetime
        )


def test_reinitialize_same_conversation_with_same_analyzer_does_not_create_new_record(
    mock_llm,
):
    """Test that reinitializing with same analyzer type does not create new history record."""  # noqa: E501
    agent = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Get initial history count
        assert len(conversation.state.security_analyzer_history) == 1
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )

        # Reinitialize with same security analyzer
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Should still have only one record since analyzer type didn't change
        assert len(conversation.state.security_analyzer_history) == 1
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )


def test_reinitialize_conversation_with_different_analyzer_creates_two_records(
    mock_llm,
):
    """Test that reinitializing with different analyzer creates two history records."""  # noqa: E501
    # Start with agent that has LLM analyzer
    agent_with_analyzer = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_with_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Verify initial state
        assert len(conversation.state.security_analyzer_history) == 1
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )

        # Switch to agent without analyzer
        agent_without_analyzer = Agent(llm=mock_llm)
        conversation._state.agent = agent_without_analyzer

        # Manually trigger init_state to simulate reinitialization
        agent_without_analyzer.init_state(conversation.state, conversation._on_event)

        # Should now have two history records
        assert len(conversation.state.security_analyzer_history) == 2, (
            "Should have two security analyzer history records"
        )

        # First record should be LLMSecurityAnalyzer
        assert (
            conversation.state.security_analyzer_history[0].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        # Second record should be None (no analyzer)
        assert conversation.state.security_analyzer_history[1].analyzer_type is None
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None


def test_reinitialize_conversation_from_none_to_analyzer_creates_two_records(mock_llm):
    """Test that reinitializing from no analyzer to analyzer creates two history records."""  # noqa: E501
    # Start with agent without analyzer
    agent_without_analyzer = Agent(llm=mock_llm)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_without_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Verify initial state
        assert len(conversation.state.security_analyzer_history) == 1
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None

        # Switch to agent with analyzer
        agent_with_analyzer = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer

        # Manually trigger init_state to simulate reinitialization
        agent_with_analyzer.init_state(conversation.state, conversation._on_event)

        # Should now have two history records
        assert len(conversation.state.security_analyzer_history) == 2, (
            "Should have two security analyzer history records"
        )

        # First record should be None (no analyzer)
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        # Second record should be LLMSecurityAnalyzer
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )


def test_multiple_reinitializations_create_appropriate_records(mock_llm):
    """Test that multiple reinitializations create the appropriate number of history records."""  # noqa: E501
    # Start with agent without analyzer
    agent_without_analyzer = Agent(llm=mock_llm)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_without_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Initial: should have 1 record (None)
        assert len(conversation.state.security_analyzer_history) == 1
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None

        # Switch to LLM analyzer
        agent_with_analyzer = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer
        agent_with_analyzer.init_state(conversation.state, conversation._on_event)

        # Should have 2 records: None, LLMSecurityAnalyzer
        assert len(conversation.state.security_analyzer_history) == 2
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )

        # Switch back to no analyzer
        agent_without_analyzer_2 = Agent(llm=mock_llm)
        conversation._state.agent = agent_without_analyzer_2
        agent_without_analyzer_2.init_state(conversation.state, conversation._on_event)

        # Should have 3 records: None, LLMSecurityAnalyzer, None
        assert len(conversation.state.security_analyzer_history) == 3
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert conversation.state.security_analyzer_history[2].analyzer_type is None
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None

        # Switch to same LLM analyzer again (should create new record since type changed)  # noqa: E501
        agent_with_analyzer_2 = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer_2
        agent_with_analyzer_2.init_state(conversation.state, conversation._on_event)

        # Should have 4 records: None, LLMSecurityAnalyzer, None, LLMSecurityAnalyzer
        assert len(conversation.state.security_analyzer_history) == 4
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert conversation.state.security_analyzer_history[2].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[3].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )


def test_security_analyzer_history_properties(mock_llm):
    """Test ConversationState security analyzer history properties and methods."""
    # Test with LLM analyzer
    agent_with_analyzer = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_with_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Test current properties
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert conversation.state.security_analyzer_history[-1].timestamp is not None
        assert isinstance(
            conversation.state.security_analyzer_history[-1].timestamp, datetime
        )

        # Test history
        assert len(conversation.state.security_analyzer_history) == 1
        record = conversation.state.security_analyzer_history[0]
        assert record.analyzer_type == "LLMSecurityAnalyzer"
        assert isinstance(record.timestamp, datetime)

    # Test without analyzer
    agent_without_analyzer = Agent(llm=mock_llm)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_without_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Test current properties
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None
        assert conversation.state.security_analyzer_history[-1].timestamp is not None
        assert isinstance(
            conversation.state.security_analyzer_history[-1].timestamp, datetime
        )

        # Test history
        assert len(conversation.state.security_analyzer_history) == 1
        record = conversation.state.security_analyzer_history[0]
        assert record.analyzer_type is None
        assert isinstance(record.timestamp, datetime)
