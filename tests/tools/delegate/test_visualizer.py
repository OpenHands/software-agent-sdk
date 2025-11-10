"""Tests for the DelegationVisualizer class."""

import json
from unittest.mock import MagicMock

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool import Action, Observation
from openhands.tools.delegate import DelegationVisualizer


class MockAction(Action):
    """Mock action for testing."""

    command: str


class MockObservation(Observation):
    """Mock observation for testing."""

    pass


def create_tool_call(call_id: str, function_name: str, args: dict) -> MessageToolCall:
    """Helper to create a MessageToolCall."""
    return MessageToolCall(
        id=call_id,
        name=function_name,
        arguments=json.dumps(args),
        origin="completion",
    )


def test_delegation_visualizer_user_message_without_sender():
    """Test user message without sender shows 'Message from User to [Agent]'."""
    visualizer = DelegationVisualizer(name="MainAgent")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    user_message = Message(role="user", content=[TextContent(text="Hello")])
    user_event = MessageEvent(source="user", llm_message=user_message)
    panel = visualizer._create_message_event_panel(user_event)

    assert panel is not None
    title = str(panel.title)
    assert "Message from User to Main Agent" in title


def test_delegation_visualizer_user_message_with_sender():
    """Test delegated message shows 'Delegator Message to Lodging Expert'."""
    visualizer = DelegationVisualizer(name="Lodging Expert")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    delegated_message = Message(
        role="user", content=[TextContent(text="Task from parent")]
    )
    delegated_event = MessageEvent(
        source="user", llm_message=delegated_message, sender="Delegator"
    )
    panel = visualizer._create_message_event_panel(delegated_event)

    assert panel is not None
    title = str(panel.title)
    assert "Delegator Message to Lodging Expert" in title


def test_delegation_visualizer_agent_response_to_user():
    """Test agent response to user shows 'Message from [Agent]'."""
    visualizer = DelegationVisualizer(name="MainAgent")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    agent_message = Message(
        role="assistant", content=[TextContent(text="Response to user")]
    )
    response_event = MessageEvent(source="agent", llm_message=agent_message)
    panel = visualizer._create_message_event_panel(response_event)

    assert panel is not None
    title = str(panel.title)
    assert "Message from Main Agent" in title


def test_delegation_visualizer_agent_response_to_delegator():
    """Test sub-agent response to parent shows 'Lodging Expert Message to Delegator'."""
    visualizer = DelegationVisualizer(name="Lodging Expert")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()

    # Set up event history with delegated message
    delegated_message = Message(
        role="user", content=[TextContent(text="Task from parent")]
    )
    delegated_event = MessageEvent(
        source="user", llm_message=delegated_message, sender="Delegator"
    )
    mock_state.events = [delegated_event]
    visualizer.initialize(mock_state)

    # Sub-agent responds
    agent_message = Message(
        role="assistant", content=[TextContent(text="Response to delegator")]
    )
    response_event = MessageEvent(source="agent", llm_message=agent_message)
    panel = visualizer._create_message_event_panel(response_event)

    assert panel is not None
    title = str(panel.title)
    assert "Lodging Expert Message to Delegator" in title


def test_delegation_visualizer_formats_agent_names():
    """Test agent names are properly formatted (snake_case to Title Case)."""
    visualizer = DelegationVisualizer(name="lodging_expert")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()

    # Set up event history with delegated message from another agent
    delegated_message = Message(
        role="user", content=[TextContent(text="Task from parent")]
    )
    delegated_event = MessageEvent(
        source="user", llm_message=delegated_message, sender="main_delegator"
    )
    mock_state.events = [delegated_event]
    visualizer.initialize(mock_state)

    # Create panel for delegated message
    panel = visualizer._create_message_event_panel(delegated_event)
    assert panel is not None
    title = str(panel.title)
    assert "Main Delegator Message to Lodging Expert" in title

    # Sub-agent responds
    agent_message = Message(
        role="assistant", content=[TextContent(text="Response to delegator")]
    )
    response_event = MessageEvent(source="agent", llm_message=agent_message)
    panel = visualizer._create_message_event_panel(response_event)

    assert panel is not None
    title = str(panel.title)
    assert "Lodging Expert Message to Main Delegator" in title


def test_delegation_visualizer_action_event():
    """Test action event shows '[Agent] Action'."""
    visualizer = DelegationVisualizer(name="MainAgent")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    action = MockAction(command="ls -la")
    tool_call = create_tool_call("call_123", "terminal", {"command": "ls -la"})
    action_event = ActionEvent(
        thought=[TextContent(text="I need to list files")],
        action=action,
        tool_name="terminal",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )
    panel = visualizer._create_event_panel(action_event)

    assert panel is not None
    title = str(panel.title)
    assert "Main Agent Action" in title


def test_delegation_visualizer_observation_event():
    """Test observation event shows '[Agent] Observation'."""
    visualizer = DelegationVisualizer(name="lodging_expert")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    observation = MockObservation(
        content=[TextContent(text="Command executed successfully")]
    )
    observation_event = ObservationEvent(
        observation=observation,
        action_id="action_123",
        tool_name="terminal",
        tool_call_id="call_123",
    )
    panel = visualizer._create_event_panel(observation_event)

    assert panel is not None
    title = str(panel.title)
    # Agent name should be formatted from snake_case to Title Case
    assert "Lodging Expert Observation" in title
