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


def test_reinitialize_same_conversation_with_same_analyzer_does_not_create_new_record(
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

        # Store the conversation ID for resuming
        conversation_id = conversation.state.id

        # Reinitialize with same security analyzer (resume from persistence)
        conversation = Conversation(
            agent=agent,
            conversation_id=conversation_id,
            persistence_dir=tmpdir,
            workspace=tmpdir,
        )

        # Without manual autosave, only the initial record is persisted
        # The migration record is created in memory but not saved to disk
        # When resuming, only the initial record is loaded, but migration happens again
        # Since the agent's security_analyzer was cleared during first initialization,
        # no migration occurs on resume, so we only have the initial record
        assert len(conversation.state.security_analyzer_history) == 1
        assert conversation.state.security_analyzer_history[0].analyzer_type is None


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

        # First record should be None (initial state)
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        # Second record should be LLMSecurityAnalyzer (migration)
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        # Third record should be None (manual change)
        assert conversation.state.security_analyzer_history[2].analyzer_type is None
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None


def test_reinitialize_conversation_from_none_to_analyzer_creates_two_records(mock_llm):
    """Test that reinitializing from no analyzer to analyzer creates two records.

    Verifies that history tracks the transition properly.
    """
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
    """Test that multiple reinitializations create appropriate history records.

    Verifies that each analyzer change is properly tracked.
    """
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
        conversation.set_security_analyzer(LLMSecurityAnalyzer())

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
        conversation.set_security_analyzer(None)

        # Should have 3 records: None, LLMSecurityAnalyzer, None
        assert len(conversation.state.security_analyzer_history) == 3
        assert conversation.state.security_analyzer_history[0].analyzer_type is None
        assert (
            conversation.state.security_analyzer_history[1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert conversation.state.security_analyzer_history[2].analyzer_type is None
        assert conversation.state.security_analyzer_history[-1].analyzer_type is None

        # Switch to same LLM analyzer again
        # (should create new record since type changed)
        conversation.set_security_analyzer(LLMSecurityAnalyzer())

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

        # Test current properties - should have 2 records (None -> LLMSecurityAnalyzer)
        assert (
            conversation.state.security_analyzer_history[-1].analyzer_type
            == "LLMSecurityAnalyzer"
        )
        assert conversation.state.security_analyzer_history[-1].timestamp is not None
        assert isinstance(
            conversation.state.security_analyzer_history[-1].timestamp, datetime
        )

        # Test history - should have 2 records
        assert len(conversation.state.security_analyzer_history) == 2
        # First record: initial None state
        record0 = conversation.state.security_analyzer_history[0]
        assert record0.analyzer_type is None
        assert isinstance(record0.timestamp, datetime)
        # Second record: migrated LLMSecurityAnalyzer
        record1 = conversation.state.security_analyzer_history[1]
        assert record1.analyzer_type == "LLMSecurityAnalyzer"
        assert isinstance(record1.timestamp, datetime)

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
