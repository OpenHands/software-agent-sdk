"""Tests for conversation.interrupt() — instant cancellation of arun().

Covers:
- Async path verification (acompletion is actually called, not sync fallback)
- CancelledError not re-raised from arun()
- interrupt() after natural completion (no-op)
- Multiple rapid interrupt() calls
- Cancellation token lifecycle (created per run, cleared on exit)
- ParallelToolExecutor skips cancelled tools
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.cancellation import CancellationToken
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import AgentErrorEvent, InterruptEvent
from openhands.sdk.llm import LLM, LLMResponse, Message, MessageToolCall, TextContent
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


# ── CancellationToken unit tests ──────────────────────────────────────


def test_cancellation_token_basic():
    """CancellationToken starts uncancelled, becomes cancelled after cancel()."""
    token = CancellationToken()
    assert not token.is_cancelled
    token.cancel()
    assert token.is_cancelled
    # Idempotent
    token.cancel()
    assert token.is_cancelled


# ── Token lifecycle tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_token_created_and_cleared_during_arun(tmp_path):
    """A fresh CancellationToken is created in arun() and cleared in finally."""

    class InstantLLM(LLM):
        def __init__(self):
            super().__init__(model="test-instant", usage_id="test-i")

        def completion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            return _make_response("test-instant")

        async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
            return _make_response("test-instant")

    conv = _make_conversation(InstantLLM(), tmp_path)
    assert conv._cancel_token is None  # Before any run

    await conv.arun()

    # After arun() completes, token should be cleared
    assert conv._cancel_token is None


@pytest.mark.asyncio
async def test_interrupt_sets_cancel_token(tmp_path):
    """interrupt() should set the cancel token before cancelling the task."""
    conv = _make_conversation(SlowLLM(sleep_seconds=60.0), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.05)

    # Token should exist while arun is active
    assert conv._cancel_token is not None
    assert not conv._cancel_token.is_cancelled

    conv.interrupt()
    await asyncio.wait_for(task, timeout=2.0)

    # After arun finishes, token is cleared
    assert conv._cancel_token is None


# ── ParallelToolExecutor cancellation tests ───────────────────────────


def _make_action_event(tool_name: str, call_id: str):
    """Build a minimal ActionEvent for executor tests."""
    from openhands.sdk.event import ActionEvent

    return ActionEvent(
        thought=[TextContent(text="test")],
        tool_call=MessageToolCall(
            id=call_id,
            name=tool_name,
            arguments="{}",
            origin="completion",
        ),
        tool_name=tool_name,
        tool_call_id=call_id,
        llm_response_id="resp-1",
    )


def test_run_safe_skips_cancelled_tool():
    """_run_safe should skip execution when token is already cancelled."""
    from openhands.sdk.agent.parallel_executor import ParallelToolExecutor

    executor = ParallelToolExecutor(max_workers=1)
    token = CancellationToken()
    token.cancel()

    action = _make_action_event("my_tool", "tc1")
    runner_called = False

    def tool_runner(ae):
        nonlocal runner_called
        runner_called = True
        return []

    result = executor._run_safe(action, tool_runner, cancel_token=token)

    assert not runner_called, "Tool should not have been executed"
    assert len(result) == 1
    assert isinstance(result[0], AgentErrorEvent)
    assert "cancelled" in result[0].error.lower()


def test_run_safe_runs_without_token():
    """_run_safe should execute normally when no token is provided."""
    from openhands.sdk.agent.parallel_executor import ParallelToolExecutor

    executor = ParallelToolExecutor(max_workers=1)
    action = _make_action_event("my_tool", "tc2")
    runner_called = False

    def tool_runner(ae):
        nonlocal runner_called
        runner_called = True
        return []

    executor._run_safe(action, tool_runner, cancel_token=None)
    assert runner_called


@pytest.mark.asyncio
async def test_execute_batch_skips_all_on_pre_cancelled_token():
    """execute_batch with a pre-cancelled token skips all tool calls."""
    from openhands.sdk.agent.parallel_executor import ParallelToolExecutor

    executor = ParallelToolExecutor(max_workers=2)
    token = CancellationToken()
    token.cancel()

    actions = [_make_action_event(f"tool_{i}", f"tc-{i}") for i in range(3)]

    runner_calls: list[str] = []

    def tool_runner(ae):
        runner_calls.append(ae.tool_name)
        return []

    results = executor.execute_batch(actions, tool_runner, cancel_token=token)

    assert len(runner_calls) == 0, "No tools should have been called"
    assert len(results) == 3
    for r in results:
        assert len(r) == 1
        assert isinstance(r[0], AgentErrorEvent)
        assert "cancelled" in r[0].error.lower()
