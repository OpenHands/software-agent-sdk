"""Tests for conversation interrupt functionality."""

import threading
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk import Agent, LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.user_action import InterruptEvent, PauseEvent
from openhands.sdk.llm import LLM


@pytest.fixture
def llm():
    """Create a test LLM instance."""
    return LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="test-conversation-llm",
        num_retries=0,
    )


@pytest.fixture
def agent(llm: LLM):
    """Create a test agent."""
    return Agent(llm=llm)


def test_interrupt_event_exists():
    """Test that InterruptEvent can be instantiated."""
    event = InterruptEvent()
    assert event.source == "user"
    assert event.reason == "User requested interrupt"


def test_interrupt_event_visualize():
    """Test InterruptEvent visualization."""
    event = InterruptEvent()
    viz = event.visualize

    assert "Interrupted" in viz.plain


def test_interrupt_event_str():
    """Test InterruptEvent string representation."""
    event = InterruptEvent()
    s = str(event)
    assert "InterruptEvent" in s
    assert "user" in s


def test_interrupt_event_custom_reason():
    """Test InterruptEvent with custom reason."""
    event = InterruptEvent(reason="Custom stop reason")
    assert event.reason == "Custom stop reason"

    viz = event.visualize
    assert "Custom stop reason" in viz.plain


def test_pause_event_vs_interrupt_event():
    """Test that PauseEvent and InterruptEvent are distinct."""
    pause = PauseEvent()
    interrupt = InterruptEvent()

    assert type(pause).__name__ == "PauseEvent"
    assert type(interrupt).__name__ == "InterruptEvent"

    # Different visualization
    assert "Paused" in pause.visualize.plain
    assert "Interrupted" in interrupt.visualize.plain


def test_conversation_has_interrupt_method(agent: Agent, tmp_path):
    """Test that LocalConversation has interrupt method."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))
    assert hasattr(conv, "interrupt")
    assert callable(conv.interrupt)


def test_conversation_interrupt_cancels_llm(agent: Agent, tmp_path):
    """Test that interrupt() calls llm.cancel()."""
    # Create conversation
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Mock the LLM's cancel method at class level
    with patch("openhands.sdk.llm.LLM.cancel") as mock_cancel:
        # Call interrupt
        conv.interrupt()

        # Verify cancel was called on the LLM
        mock_cancel.assert_called()


def test_conversation_interrupt_sets_paused_status(agent: Agent, tmp_path):
    """Test that interrupt() sets status to PAUSED."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Initially IDLE
    assert conv.state.execution_status == ConversationExecutionStatus.IDLE

    # Call interrupt
    conv.interrupt()

    # Should be PAUSED
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


def test_conversation_interrupt_when_running(agent: Agent, tmp_path):
    """Test interrupt when conversation is in RUNNING status."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Manually set to running
    conv._state.execution_status = ConversationExecutionStatus.RUNNING

    # Call interrupt
    conv.interrupt()

    # Should be PAUSED
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


def test_conversation_interrupt_idempotent(agent: Agent, tmp_path):
    """Test that multiple interrupt calls don't cause issues."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Call interrupt multiple times
    conv.interrupt()
    conv.interrupt()
    conv.interrupt()

    # Should remain PAUSED
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


def test_conversation_interrupt_cancels_all_llms_in_registry(agent: Agent, tmp_path):
    """Test that interrupt cancels LLMs in the registry too."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Add an LLM to the registry using the proper API
    extra_llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test_key"),
        usage_id="extra-llm",
        num_retries=0,
    )
    conv.llm_registry.add(extra_llm)

    # Mock cancel at class level - both calls go through the same mock
    with patch("openhands.sdk.llm.LLM.cancel") as mock_cancel:
        # Call interrupt
        conv.interrupt()

        # cancel should be called >= 2 times (agent.llm + extra_llm)
        assert mock_cancel.call_count >= 2


def test_conversation_interrupt_when_already_paused(agent: Agent, tmp_path):
    """Test interrupt when already paused still cancels LLM."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Set to PAUSED
    conv._state.execution_status = ConversationExecutionStatus.PAUSED

    # Mock cancel method at class level
    with patch("openhands.sdk.llm.LLM.cancel") as mock_cancel:
        # Call interrupt - should still cancel LLM but not change status
        conv.interrupt()

        # LLM cancel should still be called
        mock_cancel.assert_called()

    # Status should remain PAUSED
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


def test_conversation_interrupt_when_finished(agent: Agent, tmp_path):
    """Test interrupt when conversation is finished (status doesn't change)."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Set to FINISHED
    conv._state.execution_status = ConversationExecutionStatus.FINISHED

    # Mock cancel method at class level
    with patch("openhands.sdk.llm.LLM.cancel") as mock_cancel:
        # Call interrupt
        conv.interrupt()

        # LLM cancel should still be called (in case something is running)
        mock_cancel.assert_called()

    # Status should remain FINISHED
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


def test_conversation_interrupt_is_thread_safe(agent: Agent, tmp_path):
    """Test that interrupt can be called from multiple threads safely."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    # Call interrupt from multiple threads
    threads = []
    for _ in range(10):
        t = threading.Thread(target=conv.interrupt)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=2)

    # Should not raise any errors and status should be PAUSED
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


def test_conversation_register_interrupt_callback(agent: Agent, tmp_path):
    """Test that interrupt callbacks can be registered."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    called = []

    def callback():
        called.append(True)

    conv.register_interrupt_callback(callback)

    # Callback should not be called yet
    assert len(called) == 0

    # Call interrupt
    conv.interrupt()

    # Callback should have been called
    assert len(called) == 1


def test_conversation_unregister_interrupt_callback(agent: Agent, tmp_path):
    """Test that interrupt callbacks can be unregistered."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    called = []

    def callback():
        called.append(True)

    conv.register_interrupt_callback(callback)
    conv.unregister_interrupt_callback(callback)

    # Call interrupt
    conv.interrupt()

    # Callback should not have been called since it was unregistered
    assert len(called) == 0


def test_conversation_multiple_interrupt_callbacks(agent: Agent, tmp_path):
    """Test that multiple interrupt callbacks are all invoked."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    results = []

    def callback1():
        results.append("callback1")

    def callback2():
        results.append("callback2")

    conv.register_interrupt_callback(callback1)
    conv.register_interrupt_callback(callback2)

    conv.interrupt()

    # Both callbacks should have been called
    assert "callback1" in results
    assert "callback2" in results


def test_conversation_interrupt_callback_exception_handling(agent: Agent, tmp_path):
    """Test that exceptions in callbacks don't prevent other callbacks."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    results = []

    def bad_callback():
        raise RuntimeError("Intentional error")

    def good_callback():
        results.append("good")

    conv.register_interrupt_callback(bad_callback)
    conv.register_interrupt_callback(good_callback)

    # Should not raise despite bad_callback raising
    conv.interrupt()

    # good_callback should still have been called
    assert "good" in results


def test_conversation_unregister_nonexistent_callback(agent: Agent, tmp_path):
    """Test that unregistering a non-existent callback is a no-op."""
    conv = LocalConversation(agent=agent, workspace=str(tmp_path))

    def callback():
        pass

    # Should not raise
    conv.unregister_interrupt_callback(callback)
