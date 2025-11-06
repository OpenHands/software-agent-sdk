"""Tests for SecurityAnalyzerConfigurationEvent behavior in conversations.

This module tests that SecurityAnalyzerConfigurationEvent is properly created
and managed during conversation initialization and reinitialization.
"""

import tempfile

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.event.llm_convertible import SystemPromptEvent
from openhands.sdk.event.security_analyzer import SecurityAnalyzerConfigurationEvent
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
def test_new_conversation_creates_system_prompt_and_security_analyzer_events(
    request, agent_fixture, expected_analyzer_type
):
    """Test that new conversations create SystemPromptEvent and SecurityAnalyzerConfigurationEvent."""  # noqa: E501
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

        # Find SecurityAnalyzerConfigurationEvent
        security_analyzer_events = [
            e for e in events if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_analyzer_events) == 1, (
            "Should have exactly one SecurityAnalyzerConfigurationEvent"
        )

        # Verify the SecurityAnalyzerConfigurationEvent has the correct analyzer_type
        security_event = security_analyzer_events[0]
        assert security_event.analyzer_type == expected_analyzer_type
        assert security_event.source == "agent"


def test_reinitialize_same_conversation_with_same_analyzer_type_creates_new_event(
    mock_llm,
):
    """Test that reinitializing with same analyzer type creates new SecurityAnalyzerConfigurationEvent."""  # noqa: E501
    agent = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Get initial event count
        initial_security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(initial_security_events) == 1
        assert initial_security_events[0].analyzer_type == "LLMSecurityAnalyzer"

        # Reinitialize with a new agent instance (same analyzer type)
        new_agent = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())
        conversation._state.agent = new_agent

        # Manually trigger init_state to simulate reinitialization
        new_agent.init_state(conversation.state, conversation._on_event)

        # Should now have two SecurityAnalyzerConfigurationEvents (new agent instance)
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 2, (
            "Should have two SecurityAnalyzerConfigurationEvents"
        )
        assert security_events[0].analyzer_type == "LLMSecurityAnalyzer"
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"

        # Events should be different objects (different IDs)
        assert security_events[0].id != security_events[1].id


def test_reinitialize_same_conversation_with_same_agent_instance_creates_new_event(
    mock_llm,
):
    """Test that reinitializing with same agent instance creates new SecurityAnalyzerConfigurationEvent."""  # noqa: E501
    agent = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Get initial event count
        initial_security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(initial_security_events) == 1
        assert initial_security_events[0].analyzer_type == "LLMSecurityAnalyzer"

        # Reinitialize with the exact same agent instance
        # Manually trigger init_state to simulate reinitialization
        agent.init_state(conversation.state, conversation._on_event)

        # Should now have two SecurityAnalyzerConfigurationEvents
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 2, (
            "Should have two SecurityAnalyzerConfigurationEvents"
        )
        assert security_events[0].analyzer_type == "LLMSecurityAnalyzer"
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"

        # Events should be different objects (different IDs and timestamps)
        assert security_events[0].id != security_events[1].id


def test_reinitialize_conversation_with_different_analyzer_creates_two_events(mock_llm):
    """Test that reinitializing with different analyzer creates two SecurityAnalyzerConfigurationEvents."""  # noqa: E501
    # Start with agent that has LLM analyzer
    agent_with_analyzer = Agent(llm=mock_llm, security_analyzer=LLMSecurityAnalyzer())

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_with_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Verify initial state
        initial_security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(initial_security_events) == 1
        assert initial_security_events[0].analyzer_type == "LLMSecurityAnalyzer"

        # Switch to agent without analyzer
        agent_without_analyzer = Agent(llm=mock_llm)
        conversation._state.agent = agent_without_analyzer

        # Manually trigger init_state to simulate reinitialization
        agent_without_analyzer.init_state(conversation.state, conversation._on_event)

        # Should now have two SecurityAnalyzerConfigurationEvents
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 2, (
            "Should have two SecurityAnalyzerConfigurationEvents"
        )

        # First event should be LLMSecurityAnalyzer
        assert security_events[0].analyzer_type == "LLMSecurityAnalyzer"
        # Second event should be None (no analyzer)
        assert security_events[1].analyzer_type is None


def test_reinitialize_conversation_from_none_to_analyzer_creates_two_events(mock_llm):
    """Test that reinitializing from no analyzer to analyzer creates two SecurityAnalyzerConfigurationEvents."""  # noqa: E501
    # Start with agent without analyzer
    agent_without_analyzer = Agent(llm=mock_llm)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_without_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Verify initial state
        initial_security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(initial_security_events) == 1
        assert initial_security_events[0].analyzer_type is None

        # Switch to agent with analyzer
        agent_with_analyzer = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer

        # Manually trigger init_state to simulate reinitialization
        agent_with_analyzer.init_state(conversation.state, conversation._on_event)

        # Should now have two SecurityAnalyzerConfigurationEvents
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 2, (
            "Should have two SecurityAnalyzerConfigurationEvents"
        )

        # First event should be None (no analyzer)
        assert security_events[0].analyzer_type is None
        # Second event should be LLMSecurityAnalyzer
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"


def test_multiple_reinitializations_create_appropriate_events(mock_llm):
    """Test that multiple reinitializations create the appropriate number of events."""
    # Start with agent without analyzer
    agent_without_analyzer = Agent(llm=mock_llm)

    with tempfile.TemporaryDirectory() as tmpdir:
        conversation = Conversation(
            agent=agent_without_analyzer, persistence_dir=tmpdir, workspace=tmpdir
        )

        # Initial: should have 1 event (None)
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 1
        assert security_events[0].analyzer_type is None

        # Switch to LLM analyzer
        agent_with_analyzer = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer
        agent_with_analyzer.init_state(conversation.state, conversation._on_event)

        # Should have 2 events: None, LLMSecurityAnalyzer
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 2
        assert security_events[0].analyzer_type is None
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"

        # Switch back to no analyzer
        agent_without_analyzer_2 = Agent(llm=mock_llm)
        conversation._state.agent = agent_without_analyzer_2
        agent_without_analyzer_2.init_state(conversation.state, conversation._on_event)

        # Should have 3 events: None, LLMSecurityAnalyzer, None
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 3
        assert security_events[0].analyzer_type is None
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"
        assert security_events[2].analyzer_type is None

        # Switch to same LLM analyzer again (should not create duplicate)
        agent_with_analyzer_2 = Agent(
            llm=mock_llm, security_analyzer=LLMSecurityAnalyzer()
        )
        conversation._state.agent = agent_with_analyzer_2
        agent_with_analyzer_2.init_state(conversation.state, conversation._on_event)

        # Should have 4 events: None, LLMSecurityAnalyzer, None, LLMSecurityAnalyzer
        security_events = [
            e
            for e in conversation.state.events
            if isinstance(e, SecurityAnalyzerConfigurationEvent)
        ]
        assert len(security_events) == 4
        assert security_events[0].analyzer_type is None
        assert security_events[1].analyzer_type == "LLMSecurityAnalyzer"
        assert security_events[2].analyzer_type is None
        assert security_events[3].analyzer_type == "LLMSecurityAnalyzer"


def test_security_analyzer_event_properties():
    """Test SecurityAnalyzerConfigurationEvent properties and methods."""
    # Test with LLM analyzer
    llm_analyzer = LLMSecurityAnalyzer()
    event_with_analyzer = SecurityAnalyzerConfigurationEvent.from_analyzer(llm_analyzer)

    assert event_with_analyzer.analyzer_type == "LLMSecurityAnalyzer"
    assert event_with_analyzer.source == "agent"
    assert "LLMSecurityAnalyzer configured" in str(event_with_analyzer)

    # Test without analyzer
    event_without_analyzer = SecurityAnalyzerConfigurationEvent.from_analyzer(None)

    assert event_without_analyzer.analyzer_type is None
    assert event_without_analyzer.source == "agent"
    assert "No security analyzer configured" in str(event_without_analyzer)
