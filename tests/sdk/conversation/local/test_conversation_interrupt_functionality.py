"""
Unit tests for interrupt functionality.

Tests the interrupt mechanism that allows immediate termination of
LLM calls and tool executions.

Key requirements:
1. interrupt() method sets state to PAUSED and emits InterruptEvent
2. interrupt() closes HTTP clients used by LLMs
3. interrupt() is thread-safe and can be called from signal handlers
4. Multiple interrupt calls only create one InterruptEvent
"""

import threading

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.cancellation import CancellationError, CancellationToken
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import InterruptEvent
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm_registry import LLMRegistry


class TestCancellationToken:
    """Tests for CancellationToken class."""

    def test_initial_state_not_cancelled(self):
        """Token should start in non-cancelled state."""
        token = CancellationToken()
        assert not token.is_cancelled()

    def test_cancel_sets_cancelled_flag(self):
        """Calling cancel() should set the cancelled flag."""
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled()

    def test_cancel_is_idempotent(self):
        """Multiple cancel calls should have no additional effect."""
        token = CancellationToken()
        token.cancel()
        token.cancel()
        token.cancel()
        assert token.is_cancelled()

    def test_callback_invoked_on_cancel(self):
        """Registered callbacks should be invoked on cancel."""
        token = CancellationToken()
        callback_called = threading.Event()

        def callback():
            callback_called.set()

        token.register_callback(callback)
        token.cancel()
        assert callback_called.is_set()

    def test_callback_invoked_immediately_if_already_cancelled(self):
        """Callback should be invoked immediately if token already cancelled."""
        token = CancellationToken()
        token.cancel()

        callback_called = threading.Event()

        def callback():
            callback_called.set()

        token.register_callback(callback)
        assert callback_called.is_set()

    def test_throw_if_cancelled_raises_when_cancelled(self):
        """throw_if_cancelled() should raise CancellationError when cancelled."""
        token = CancellationToken()
        token.cancel()

        with pytest.raises(CancellationError):
            token.throw_if_cancelled()

    def test_throw_if_cancelled_does_not_raise_when_not_cancelled(self):
        """throw_if_cancelled() should not raise when not cancelled."""
        token = CancellationToken()
        token.throw_if_cancelled()  # Should not raise

    def test_unregister_callback(self):
        """Callback should not be invoked after unregistration."""
        token = CancellationToken()
        callback_called = threading.Event()

        def callback():
            callback_called.set()

        token.register_callback(callback)
        result = token.unregister_callback(callback)
        assert result is True

        token.cancel()
        assert not callback_called.is_set()

    def test_unregister_nonexistent_callback(self):
        """Unregistering non-existent callback should return False."""
        token = CancellationToken()

        def callback():
            pass

        result = token.unregister_callback(callback)
        assert result is False

    def test_wait_returns_true_when_cancelled(self):
        """wait() should return True when token is cancelled."""
        token = CancellationToken()

        def cancel_after_delay():
            import time

            time.sleep(0.1)
            token.cancel()

        t = threading.Thread(target=cancel_after_delay)
        t.start()

        result = token.wait(timeout=1.0)
        assert result is True
        t.join()

    def test_wait_returns_false_on_timeout(self):
        """wait() should return False when timeout expires."""
        token = CancellationToken()
        result = token.wait(timeout=0.1)
        assert result is False

    def test_child_token_cancelled_when_parent_cancelled(self):
        """Child token should be cancelled when parent is cancelled."""
        parent = CancellationToken()
        child = parent.child_token()

        assert not child.is_cancelled()
        parent.cancel()
        assert child.is_cancelled()

    def test_child_token_can_be_cancelled_independently(self):
        """Child token can be cancelled without affecting parent."""
        parent = CancellationToken()
        child = parent.child_token()

        child.cancel()
        assert child.is_cancelled()
        assert not parent.is_cancelled()

    def test_thread_safety_multiple_cancels(self):
        """Multiple threads cancelling should be safe."""
        token = CancellationToken()
        callback_count = []
        lock = threading.Lock()

        def callback():
            with lock:
                callback_count.append(1)

        token.register_callback(callback)

        threads = [threading.Thread(target=token.cancel) for _ in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Callback should only be invoked once
        assert len(callback_count) == 1


class TestInterruptFunctionality:
    """Test suite for interrupt functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="test-llm",
        )

        self.agent = Agent(llm=self.llm, tools=[])
        self.conversation: LocalConversation = Conversation(agent=self.agent)

    def test_interrupt_basic_functionality(self):
        """Test basic interrupt operations."""
        # Test initial state
        assert (
            self.conversation.state.execution_status == ConversationExecutionStatus.IDLE
        )

        # Test interrupt method
        self.conversation.interrupt("Test interrupt")

        # Should be paused
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # Should have InterruptEvent
        interrupt_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, InterruptEvent)
        ]
        assert len(interrupt_events) == 1
        assert interrupt_events[0].source == "user"
        assert interrupt_events[0].detail == "Test interrupt"

    def test_interrupt_without_reason(self):
        """Test interrupt with default reason."""
        self.conversation.interrupt()

        interrupt_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, InterruptEvent)
        ]
        assert len(interrupt_events) == 1
        assert interrupt_events[0].detail == "User requested interrupt"

    def test_multiple_interrupt_calls_create_one_event(self):
        """Test that multiple interrupt calls only create one InterruptEvent."""
        self.conversation.interrupt("First")
        self.conversation.interrupt("Second")
        self.conversation.interrupt("Third")

        # Should have only ONE interrupt event (similar to pause behavior)
        interrupt_events = [
            event
            for event in self.conversation.state.events
            if isinstance(event, InterruptEvent)
        ]
        assert len(interrupt_events) == 1

    def test_interrupt_does_not_affect_already_paused(self):
        """Test interrupt on already paused conversation."""
        # First pause
        self.conversation.pause()
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )

        # Interrupt should still work (adds event but state already paused)
        self.conversation.interrupt("Test")

        # State still paused
        assert (
            self.conversation.state.execution_status
            == ConversationExecutionStatus.PAUSED
        )


class TestLLMInterrupt:
    """Test LLM interrupt functionality."""

    def test_llm_interrupt_closes_http_client(self):
        """Test that LLM.interrupt() closes the HTTP client."""
        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="test-interrupt-llm",
        )

        # Create HTTP client by accessing it
        client = llm._get_or_create_http_client()
        assert not client.is_closed

        # Interrupt should close it
        llm.interrupt()

        # Client should now be closed or None
        assert llm._http_client is None

    def test_llm_interrupt_is_idempotent(self):
        """Test that multiple LLM.interrupt() calls are safe."""
        llm = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="test-interrupt-llm-2",
        )

        # Create HTTP client
        llm._get_or_create_http_client()

        # Multiple interrupts should be safe
        llm.interrupt()
        llm.interrupt()
        llm.interrupt()

        assert llm._http_client is None


class TestLLMRegistryAllLLMs:
    """Test LLMRegistry.all_llms() method."""

    def test_all_llms_empty_registry(self):
        """Test all_llms on empty registry."""
        registry = LLMRegistry()
        assert registry.all_llms() == []

    def test_all_llms_returns_all_registered(self):
        """Test all_llms returns all registered LLMs."""
        registry = LLMRegistry()

        llm1 = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="llm-1",
        )
        llm2 = LLM(
            model="gpt-4o-mini",
            api_key=SecretStr("test-key"),
            usage_id="llm-2",
        )

        registry.add(llm1)
        registry.add(llm2)

        all_llms = registry.all_llms()
        assert len(all_llms) == 2
        assert llm1 in all_llms
        assert llm2 in all_llms


class TestInterruptDuringAgentStep:
    """Test that interrupt during agent.step() discards response and skips execution."""

    def test_interrupt_after_llm_completion_discards_response(self):
        """Verify that agent.step() checks interrupt status after LLM returns.

        Uses mock patching to set PAUSED status right after LLM completion,
        verifying the check in agent.step() prevents finish tool execution.
        """
        from unittest.mock import patch

        from openhands.sdk.llm import Message, MessageToolCall, TextContent
        from openhands.sdk.testing import TestLLM

        llm = TestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_1",
                            name="finish",
                            arguments='{"message": "Should not be executed"}',
                            origin="completion",
                        )
                    ],
                ),
            ]
        )

        agent = Agent(llm=llm, tools=[])
        conversation = Conversation(agent=agent, stuck_detection=False)

        conversation.send_message(
            Message(role="user", content=[TextContent(text="Run")])
        )

        from openhands.sdk.agent.utils import make_llm_completion

        original_make_completion = make_llm_completion

        def make_completion_then_pause(*args, **kwargs):
            result = original_make_completion(*args, **kwargs)
            conversation._state.execution_status = ConversationExecutionStatus.PAUSED
            return result

        with patch(
            "openhands.sdk.agent.agent.make_llm_completion",
            side_effect=make_completion_then_pause,
        ):
            conversation.run()

        # Status should be PAUSED (we set it in our patch)
        # If the finish tool had executed, status would be FINISHED
        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED

    def test_run_loop_breaks_when_paused_between_iterations(self):
        """Verify run loop exits when status is set to PAUSED between iterations."""
        from unittest.mock import patch

        from openhands.sdk.llm import Message, MessageToolCall, TextContent
        from openhands.sdk.testing import TestLLM

        llm = TestLLM.from_messages(
            [
                # First response: think tool (doesn't change status)
                Message(
                    role="assistant",
                    content=[TextContent(text="")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_1",
                            name="think",
                            arguments='{"thought": "thinking..."}',
                            origin="completion",
                        )
                    ],
                ),
                # Second response: should NOT be called because we set PAUSED
                Message(
                    role="assistant",
                    content=[TextContent(text="")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_2",
                            name="finish",
                            arguments='{"message": "done"}',
                            origin="completion",
                        )
                    ],
                ),
            ]
        )

        agent = Agent(llm=llm, tools=[])
        conversation = Conversation(agent=agent, stuck_detection=False)

        conversation.send_message(
            Message(role="user", content=[TextContent(text="Run")])
        )

        # Patch make_llm_completion to set PAUSED after first call
        from openhands.sdk.agent.utils import make_llm_completion

        original_make_completion = make_llm_completion
        call_count = [0]

        def make_completion_pause_after_first(*args, **kwargs):
            result = original_make_completion(*args, **kwargs)
            call_count[0] += 1
            if call_count[0] == 1:
                # After first LLM call, set PAUSED
                conversation._state.execution_status = (
                    ConversationExecutionStatus.PAUSED
                )
            return result

        with patch(
            "openhands.sdk.agent.agent.make_llm_completion",
            side_effect=make_completion_pause_after_first,
        ):
            conversation.run()

        # First LLM call made
        assert call_count[0] == 1

        # Run loop should have exited due to PAUSED status
        assert conversation.state.execution_status == ConversationExecutionStatus.PAUSED

        # Second LLM response should NOT have been consumed
        assert llm.remaining_responses == 1
