"""Tests for the DelegationVisualizer class."""

import json
from unittest.mock import MagicMock

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.tool import Action, Observation
from openhands.tools.delegate import DelegationVisualizer


class MockDelegateAction(Action):
    """Mock action for testing."""

    command: str = "test command"


class MockDelegateObservation(Observation):
    """Mock observation for testing."""

    result: str = "test result"


def create_tool_call(
    call_id: str, function_name: str, arguments: dict
) -> MessageToolCall:
    """Helper to create a MessageToolCall."""
    return MessageToolCall(
        id=call_id,
        name=function_name,
        arguments=json.dumps(arguments),
        origin="completion",
    )


def test_delegation_visualizer_user_message_without_sender():
    """Test user message without sender shows 'User Message to [Agent] Agent'."""
    visualizer = DelegationVisualizer(name="MainAgent")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    user_message = Message(role="user", content=[TextContent(text="Hello")])
    user_event = MessageEvent(source="user", llm_message=user_message)
    block = visualizer._create_message_event_block(user_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "User Message to Main Agent Agent" in str(block.renderables[0])


def test_delegation_visualizer_user_message_with_sender():
    """Test delegated message shows sender and receiver agent names."""  # noqa: E501
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
    block = visualizer._create_message_event_block(delegated_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Delegator Agent Message to Lodging Expert Agent" in str(
        block.renderables[0]
    )


def test_delegation_visualizer_agent_response_to_user():
    """Test agent response to user shows 'Message from [Agent] Agent to User'."""
    visualizer = DelegationVisualizer(name="MainAgent")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    agent_message = Message(
        role="assistant", content=[TextContent(text="Response to user")]
    )
    response_event = MessageEvent(source="agent", llm_message=agent_message)
    block = visualizer._create_message_event_block(response_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Message from Main Agent Agent to User" in str(block.renderables[0])


def test_delegation_visualizer_agent_response_to_delegator():
    """Test sub-agent response to parent shows sender and receiver."""  # noqa: E501
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
    block = visualizer._create_message_event_block(response_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Lodging Expert Agent Message to Delegator Agent" in str(
        block.renderables[0]
    )


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

    # Create block for delegated message
    block = visualizer._create_message_event_block(delegated_event)
    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Main Delegator Agent Message to Lodging Expert Agent" in str(
        block.renderables[0]
    )

    # Sub-agent responds
    agent_message = Message(
        role="assistant", content=[TextContent(text="Response to delegator")]
    )
    response_event = MessageEvent(source="agent", llm_message=agent_message)
    block = visualizer._create_message_event_block(response_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Lodging Expert Agent Message to Main Delegator Agent" in str(
        block.renderables[0]
    )


def test_delegation_visualizer_action_event():
    """Test action event shows agent name in title."""
    visualizer = DelegationVisualizer(name="lodging_expert")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    # Create a proper action event
    action = MockDelegateAction(command="search hotels")
    tool_call = create_tool_call("call_123", "search", {"command": "search hotels"})
    action_event = ActionEvent(
        thought=[TextContent(text="Searching for hotels")],
        action=action,
        tool_name="search",
        tool_call_id="call_123",
        tool_call=tool_call,
        llm_response_id="response_456",
    )

    block = visualizer._create_event_block(action_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Lodging Expert Agent Action" in str(block.renderables[0])


def test_delegation_visualizer_observation_event():
    """Test observation event shows agent name in title."""
    visualizer = DelegationVisualizer(name="main_delegator")
    mock_state = MagicMock()
    mock_state.stats = ConversationStats()
    mock_state.events = []
    visualizer.initialize(mock_state)

    # Create a proper observation event
    observation = MockDelegateObservation(result="Hotel search results")
    observation_event = ObservationEvent(
        source="environment",
        observation=observation,
        tool_name="search",
        tool_call_id="call_123",
        action_id="action_789",
    )

    block = visualizer._create_event_block(observation_event)

    assert block is not None
    # The block contains the Rule as the first element with the title
    assert "Main Delegator Agent Observation" in str(block.renderables[0])
