"""Tests for the DelegationVisualizer class."""

from unittest.mock import MagicMock

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.tools.delegate import DelegationVisualizer


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
