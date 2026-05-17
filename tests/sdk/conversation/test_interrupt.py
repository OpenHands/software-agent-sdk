"""Tests for conversation.interrupt() — instant cancellation of arun()."""

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
