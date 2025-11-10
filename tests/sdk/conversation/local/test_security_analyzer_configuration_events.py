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
    """Test that new conversations set security analyzer configuration.

    Verifies that ConversationState is properly configured.
    """
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
        if expected_analyzer_type is None:
            # Agent without analyzer: should have 1 record with None
            assert len(conversation.state.security_analyzer_history) == 1
            assert conversation.state.security_analyzer_history[0].analyzer_type is None
        else:
            # Agent with analyzer: should have 2 records (None -> LLMSecurityAnalyzer)
            assert len(conversation.state.security_analyzer_history) == 2
            assert conversation.state.security_analyzer_history[0].analyzer_type is None
            assert (
                conversation.state.security_analyzer_history[1].analyzer_type
                == expected_analyzer_type
            )

        # Final state should match expected analyzer type
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == expected_analyzer_type
        )
        assert isinstance(
            conversation.state.security_analyzer_history[0].timestamp, datetime
        )


def test_reinitialize_same_analyzer_does_not_create_new_record(
    mock_llm,
):
    """Test that reinitializing with same analyzer type does not create new record.

    Verifies that no duplicate history records are created.
    """
    agent = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Get initial history count - should have 2 records
        # (None -> LLMSecurityAnalyzer)
        assert len(conversation.state.security_analyzer_history) == 2
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )

        # Reinitialize with same security analyzer (resume from persistence)
        conversation.set_security_analyzer(LLMSecurityAnalyzer())
        # No change to analyzer history
        assert len(conversation.state.security_analyzer_history) == 2


def test_reinitialize_conversation_with_different_analyzer_creates_two_records(
    mock_llm,
):
    """Test that reinitializing with different analyzer creates two history records."""
    # Start with agent that has LLM analyzer
    agent_with_analyzer = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_with_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Verify initial state - should have 2 records (None -> LLMSecurityAnalyzer)
        assert len(conversation.state.security_analyzer_history) == 2
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )

        # Switch to agent without analyzer by setting security analyzer to None
        conversation.set_security_analyzer(None)

        # Should now have three history records (None -> LLMSecurityAnalyzer -> None)
        assert len(conversation.state.security_analyzer_history) == 3, (
            "Should have three security analyzer history records"
        )
        assert conversation.state.security_analyzer_history[2].analyzer_type is None
