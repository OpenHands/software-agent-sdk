"""Tests for the Conversation class visualize parameter."""

from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.visualizer import ConversationVisualizer
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.llm import LLM, Message, TextContent


def create_test_event(content: str = "Test event content") -> MessageEvent:
    """Create a test MessageEvent for testing."""
    return MessageEvent(
        llm_message=Message(role="user", content=[TextContent(text=content)]),
        source="user",
    )


@pytest.fixture
def mock_agent():
    """Create a real agent for testing."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return agent


def test_conversation_with_visualize_true(mock_agent):
    """Test Conversation with visualize=True (default)."""
    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(agent=mock_agent, visualize=True)

        # Should have a visualizer
        assert conversation._visualizer is not None
        assert isinstance(conversation._visualizer, ConversationVisualizer)

        # Agent should be initialized with callbacks that include visualizer
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        assert "on_event" in kwargs

        # The on_event callback should be composed of multiple callbacks
        on_event = kwargs["on_event"]
        assert callable(on_event)


def test_conversation_with_visualize_false(mock_agent):
    """Test Conversation with visualize=False."""
    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(agent=mock_agent, visualize=False)

        # Should not have a visualizer
        assert conversation._visualizer is None

        # Agent should still be initialized with callbacks (just not visualizer)
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        assert "on_event" in kwargs

        # The on_event callback should still exist (for state persistence)
        on_event = kwargs["on_event"]
        assert callable(on_event)


def test_conversation_default_visualize_is_true(mock_agent):
    """Test that visualize defaults to True."""
    with patch.object(Agent, "init_state"):
        conversation = Conversation(agent=mock_agent)

        # Should have a visualizer by default
        assert conversation._visualizer is not None
        assert isinstance(conversation._visualizer, ConversationVisualizer)


def test_conversation_with_custom_callbacks_and_visualize_true(mock_agent):
    """Test Conversation with custom callbacks and visualize=True."""
    custom_callback = Mock()
    callbacks = [custom_callback]

    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(
            agent=mock_agent, callbacks=callbacks, visualize=True
        )

        # Should have a visualizer
        assert conversation._visualizer is not None

        # Test that callbacks are composed correctly by triggering an event
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        on_event = kwargs["on_event"]

        # Create a test event
        test_event = create_test_event("Test event content")
        on_event(test_event)

        # Custom callback should have been called
        custom_callback.assert_called_once_with(test_event)

        # Event should be in conversation state
        assert test_event in conversation.state.events


def test_conversation_with_custom_callbacks_and_visualize_false(mock_agent):
    """Test Conversation with custom callbacks and visualize=False."""
    custom_callback = Mock()
    callbacks = [custom_callback]

    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(
            agent=mock_agent, callbacks=callbacks, visualize=False
        )

        # Should not have a visualizer
        assert conversation._visualizer is None

        # Test that callbacks are composed correctly
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        on_event = kwargs["on_event"]

        # Create a test event and trigger it
        test_event = create_test_event("Test event content")
        on_event(test_event)

        # Custom callback should have been called
        custom_callback.assert_called_once_with(test_event)

        # Event should be in conversation state
        assert test_event in conversation.state.events


def test_conversation_callback_order(mock_agent):
    """Test that callbacks are executed in the correct order."""
    call_order = []

    def callback1(event):
        call_order.append("callback1")

    def callback2(event):
        call_order.append("callback2")

    # Mock the visualizer to track when it's called
    with (
        patch(
            "openhands.sdk.conversation.impl.local_conversation.create_default_visualizer"
        ) as mock_create_viz,
        patch.object(Agent, "init_state") as mock_init_state,
    ):
        mock_visualizer = Mock()
        mock_visualizer.on_event = Mock(
            side_effect=lambda e: call_order.append("visualizer")
        )
        mock_create_viz.return_value = mock_visualizer

        conversation = Conversation(
            agent=mock_agent, callbacks=[callback1, callback2], visualize=True
        )

        # Get the composed callback
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        on_event = kwargs["on_event"]

        # Trigger an event
        test_event = create_test_event("Test event content")
        on_event(test_event)

        # Check order: visualizer, callback1, callback2, then state persistence
        assert call_order == ["visualizer", "callback1", "callback2"]

        # Event should be in state (state persistence happens last)
        assert test_event in conversation.state.events


def test_conversation_no_callbacks_with_visualize_true(mock_agent):
    """Test Conversation with no custom callbacks but visualize=True."""
    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(agent=mock_agent, callbacks=None, visualize=True)

        # Should have a visualizer
        assert conversation._visualizer is not None

        # Should still work with just visualizer and state persistence
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        on_event = kwargs["on_event"]

        # Should be able to handle events
        test_event = create_test_event("Test event content")
        on_event(test_event)

        # Event should be in state
        assert test_event in conversation.state.events


def test_conversation_no_callbacks_with_visualize_false(mock_agent):
    """Test Conversation with no custom callbacks and visualize=False."""
    with patch.object(Agent, "init_state") as mock_init_state:
        conversation = Conversation(agent=mock_agent, callbacks=None, visualize=False)

        # Should not have a visualizer
        assert conversation._visualizer is None

        # Should still work with just state persistence
        mock_init_state.assert_called_once()
        args, kwargs = mock_init_state.call_args
        on_event = kwargs["on_event"]

        # Should be able to handle events
        test_event = create_test_event("Test event content")
        on_event(test_event)

        # Event should be in state
        assert test_event in conversation.state.events
