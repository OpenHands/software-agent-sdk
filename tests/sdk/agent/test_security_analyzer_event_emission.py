"""Tests for SecurityAnalyzerConfigurationEvent emission during init_state."""

from unittest.mock import Mock

from openhands.sdk import LLM, Conversation
from openhands.sdk.agent import Agent
from openhands.sdk.event.llm_convertible import ActionEvent
from openhands.sdk.event.security_analyzer import SecurityAnalyzerConfigurationEvent
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.security.risk import SecurityRisk


class MockSecurityAnalyzer(SecurityAnalyzerBase):
    """Mock security analyzer for testing."""

    def security_risk(self, action: ActionEvent) -> SecurityRisk:
        return SecurityRisk.LOW


def test_init_state_emits_security_analyzer_event_with_analyzer(tmp_path):
    """Test that init_state emits SecurityAnalyzerConfigurationEvent when analyzer is configured."""
    # Create agent with security analyzer
    analyzer = MockSecurityAnalyzer()
    agent = Agent(
        llm=LLM(model="test"),
        security_analyzer=analyzer,
    )

    # Create conversation and capture events
    captured_events = []

    def capture_event(event):
        captured_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[capture_event],
        visualize=False,
    )

    # Check that SecurityAnalyzerConfigurationEvent was emitted
    security_events = [
        event
        for event in captured_events
        if isinstance(event, SecurityAnalyzerConfigurationEvent)
    ]

    assert len(security_events) == 1
    security_event = security_events[0]
    assert security_event.analyzer_type == "MockSecurityAnalyzer"
    assert security_event.source == "agent"


def test_init_state_emits_security_analyzer_event_with_llm_analyzer(tmp_path):
    """Test that init_state emits SecurityAnalyzerConfigurationEvent with LLMSecurityAnalyzer."""
    # Create agent with LLM security analyzer
    analyzer = LLMSecurityAnalyzer()
    agent = Agent(
        llm=LLM(model="test"),
        security_analyzer=analyzer,
    )

    # Create conversation and capture events
    captured_events = []

    def capture_event(event):
        captured_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[capture_event],
        visualize=False,
    )

    # Check that SecurityAnalyzerConfigurationEvent was emitted
    security_events = [
        event
        for event in captured_events
        if isinstance(event, SecurityAnalyzerConfigurationEvent)
    ]

    assert len(security_events) == 1
    security_event = security_events[0]
    assert security_event.analyzer_type == "LLMSecurityAnalyzer"
    assert security_event.source == "agent"


def test_init_state_emits_security_analyzer_event_without_analyzer(tmp_path):
    """Test that init_state emits SecurityAnalyzerConfigurationEvent when no analyzer is configured."""
    # Create agent without security analyzer
    agent = Agent(
        llm=LLM(model="test"),
        security_analyzer=None,
    )

    # Create conversation and capture events
    captured_events = []

    def capture_event(event):
        captured_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[capture_event],
        visualize=False,
    )

    # Check that SecurityAnalyzerConfigurationEvent was emitted
    security_events = [
        event
        for event in captured_events
        if isinstance(event, SecurityAnalyzerConfigurationEvent)
    ]

    assert len(security_events) == 1
    security_event = security_events[0]
    assert security_event.analyzer_type is None
    assert security_event.source == "agent"


def test_init_state_emits_security_analyzer_event_exactly_once(tmp_path):
    """Test that init_state emits SecurityAnalyzerConfigurationEvent exactly once."""
    # Create agent with security analyzer
    analyzer = MockSecurityAnalyzer()
    agent = Agent(
        llm=LLM(model="test"),
        security_analyzer=analyzer,
    )

    # Create conversation and capture events
    captured_events = []

    def capture_event(event):
        captured_events.append(event)

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[capture_event],
        visualize=False,
    )

    # Check that exactly one SecurityAnalyzerConfigurationEvent was emitted
    security_events = [
        event
        for event in captured_events
        if isinstance(event, SecurityAnalyzerConfigurationEvent)
    ]

    assert len(security_events) == 1, (
        f"Expected exactly 1 SecurityAnalyzerConfigurationEvent, got {len(security_events)}"
    )


def test_security_analyzer_event_callback_receives_correct_event(tmp_path):
    """Test that the callback receives the correct SecurityAnalyzerConfigurationEvent."""
    analyzer = MockSecurityAnalyzer()
    agent = Agent(
        llm=LLM(model="test"),
        security_analyzer=analyzer,
    )

    # Mock callback to verify event details
    mock_callback = Mock()

    conversation = Conversation(
        agent=agent,
        workspace=str(tmp_path),
        callbacks=[mock_callback],
        visualize=False,
    )

    # Verify that the callback was called with SecurityAnalyzerConfigurationEvent
    security_analyzer_calls = [
        call
        for call in mock_callback.call_args_list
        if len(call.args) > 0
        and isinstance(call.args[0], SecurityAnalyzerConfigurationEvent)
    ]

    assert len(security_analyzer_calls) == 1
    event = security_analyzer_calls[0].args[0]
    assert event.analyzer_type == "MockSecurityAnalyzer"
    assert event.source == "agent"
