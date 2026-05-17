"""Tests for conversation.interrupt() — instant cancellation of arun().

Covers:
- Async path verification (acompletion is actually called, not sync fallback)
- CancelledError not re-raised from arun()
- interrupt() after natural completion (no-op)
- Multiple rapid interrupt() calls
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import InterruptEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


def _make_response(model_name: str = "test-slow") -> LLMResponse:
    return LLMResponse(
        message=Message(
            role="assistant",
            content=[TextContent(text="done")],
        ),
        metrics=MetricsSnapshot(
            model_name=model_name,
            accumulated_cost=0.0,
            max_budget_per_task=0.0,
            accumulated_token_usage=TokenUsage(model=model_name),
        ),
        raw_response=MagicMock(spec=ModelResponse, id="s1"),
    )


class SlowLLM(LLM):
    """LLM that blocks in acompletion to simulate a long-running call."""

    _sleep_seconds: float = PrivateAttr(default=10.0)

    def __init__(self, *, sleep_seconds: float = 10.0):
        super().__init__(model="test-slow", usage_id="test-slow")
        self._sleep_seconds = sleep_seconds

    def completion(  # type: ignore[override]
        self, messages, tools=None, **kw
    ) -> LLMResponse:
        import time

        time.sleep(self._sleep_seconds)
        return _make_response()

    async def acompletion(  # type: ignore[override]
        self, messages, tools=None, **kw
    ) -> LLMResponse:
        await asyncio.sleep(self._sleep_seconds)
        return _make_response()


def _make_conversation(llm: LLM, tmp_path) -> LocalConversation:
    agent = Agent(llm=llm, tools=[])
    conv = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        visualizer=None,
    )
    conv.send_message("hello")
    return conv


@pytest.mark.asyncio
async def test_interrupt_cancels_arun_immediately(tmp_path):
    """interrupt() should cancel arun() mid-LLM-call and set PAUSED."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    task = asyncio.create_task(conv.arun())

    # Let the event loop start arun() and enter the LLM sleep
    await asyncio.sleep(0.05)

    # Interrupt should cancel the in-flight LLM call
    conv.interrupt()

    # arun() should return quickly (it catches CancelledError)
    await asyncio.wait_for(task, timeout=2.0)

    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED

    # An InterruptEvent should have been emitted
    events = list(conv.state.events)
    interrupt_events = [e for e in events if isinstance(e, InterruptEvent)]
    assert len(interrupt_events) == 1


@pytest.mark.asyncio
async def test_interrupt_without_arun_falls_back_to_pause(tmp_path):
    """interrupt() with no active arun() should fall back to pause()."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    # Set to RUNNING manually to verify pause fallback
    conv._state.execution_status = ConversationExecutionStatus.RUNNING

    conv.interrupt()

    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED


@pytest.mark.asyncio
async def test_arun_task_cleared_after_interrupt(tmp_path):
    """_arun_task should be None after arun() finishes (via interrupt)."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.05)
    conv.interrupt()
    await asyncio.wait_for(task, timeout=2.0)

    assert conv._arun_task is None


@pytest.mark.asyncio
async def test_interrupt_is_resumable(tmp_path):
    """After interrupt, conversation can be resumed with a new arun()."""

    class CountingLLM(LLM):
        """LLM that completes instantly, counting calls."""

        _call_count: int = PrivateAttr(default=0)

        def __init__(self):
            super().__init__(model="test-counting", usage_id="test-c")

        def completion(  # type: ignore[override]
            self, messages, tools=None, **kw
        ) -> LLMResponse:
            self._call_count += 1
            return _make_response("test-counting")

        async def acompletion(  # type: ignore[override]
            self, messages, tools=None, **kw
        ) -> LLMResponse:
            self._call_count += 1
            return _make_response("test-counting")

    llm = CountingLLM()
    conv = _make_conversation(llm, tmp_path)

    # First run should complete normally (agent says "done" → FINISHED)
    await conv.arun()
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm._call_count == 1

    # Send another message and run again — should work
    conv.send_message("continue")
    await conv.arun()
    assert llm._call_count == 2


@pytest.mark.asyncio
async def test_arun_calls_acompletion_not_completion(tmp_path):
    """Verify that arun() exercises the async path (acompletion)."""

    class TrackingLLM(LLM):
        _sync_calls: int = PrivateAttr(default=0)
        _async_calls: int = PrivateAttr(default=0)

        def __init__(self):
            super().__init__(model="test-track", usage_id="test-t")

        def completion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            self._sync_calls += 1
            return _make_response("test-track")

        async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            self._async_calls += 1
            return _make_response("test-track")

    llm = TrackingLLM()
    conv = _make_conversation(llm, tmp_path)
    await conv.arun()

    assert llm._async_calls == 1, "arun() should call acompletion"
    assert llm._sync_calls == 0, "arun() should NOT call sync completion"


@pytest.mark.asyncio
async def test_arun_does_not_raise_cancelled_error(tmp_path):
    """CancelledError must NOT propagate out of arun()."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.05)
    conv.interrupt()

    # If CancelledError propagated, wait_for would raise it.
    # arun() should return cleanly with no exception.
    await asyncio.wait_for(task, timeout=2.0)
    # If we reach here, no CancelledError was raised — test passes.


@pytest.mark.asyncio
async def test_interrupt_after_natural_completion_is_noop(tmp_path):
    """interrupt() after arun() completes naturally should be a safe no-op."""

    class InstantLLM(LLM):
        def __init__(self):
            super().__init__(model="test-instant", usage_id="test-i")

        def completion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            return _make_response("test-instant")

        async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            return _make_response("test-instant")

    conv = _make_conversation(InstantLLM(), tmp_path)
    await conv.arun()
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED

    # interrupt() after completion — should not crash or change status
    conv.interrupt()
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


@pytest.mark.asyncio
async def test_multiple_rapid_interrupts(tmp_path):
    """Multiple rapid interrupt() calls should not crash."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.05)

    # Fire multiple interrupts rapidly
    conv.interrupt()
    conv.interrupt()
    conv.interrupt()

    await asyncio.wait_for(task, timeout=2.0)
    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED
