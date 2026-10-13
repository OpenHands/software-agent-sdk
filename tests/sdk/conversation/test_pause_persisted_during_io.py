"""Tests for #5626: pause during in-flight LLM call must persist to base_state.json.

When the run loop releases the state lock for an awaited LLM network call,
a concurrent pause() must immediately persist the new execution_status to
disk (base_state.json), not defer it until the step ends.

All synchronization uses asyncio.Events for determinism — no wall-clock sleeps.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.persistence_const import BASE_STATE
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage


def _make_response(model_name: str = "test-llm") -> LLMResponse:
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


class ControlledLLM(LLM):
    """LLM that signals entry via ``entered`` and waits on ``release``.

    Tests ``await entered`` to know the state lock has been released for I/O,
    then perform concurrent actions, then set ``release`` to let the LLM return.
    """

    _entered: asyncio.Event = PrivateAttr()
    _release: asyncio.Event = PrivateAttr()
    _should_raise: bool = PrivateAttr(default=False)

    def __init__(self, should_raise: bool = False):
        super().__init__(model="test-controlled", usage_id="test-controlled")
        self._entered = asyncio.Event()
        self._release = asyncio.Event()
        self._should_raise = should_raise

    @property
    def entered(self) -> asyncio.Event:
        return self._entered

    @property
    def release(self) -> asyncio.Event:
        return self._release

    async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
        self._entered.set()
        await self._release.wait()
        if self._should_raise:
            raise RuntimeError("LLM transient error")
        return _make_response("test-controlled")


class InstantLLM(LLM):
    """LLM that completes immediately, no event coordination."""

    def __init__(self):
        super().__init__(model="test-instant", usage_id="test-instant")

    async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
        return _make_response("test-instant")


def _make_conversation(llm: LLM, tmp_path) -> LocalConversation:
    agent = Agent(llm=llm, tools=[])
    persistence_base = str(tmp_path / "conversations")
    conv = LocalConversation(
        agent=agent,
        workspace=str(tmp_path),
        persistence_dir=persistence_base,
        visualizer=None,
    )
    conv.send_message("hello")
    return conv


def _base_state_path(conv: LocalConversation) -> Path:
    assert conv._state.persistence_dir is not None, (
        "persistence_dir must be set for file-backed conversations"
    )
    return Path(conv._state.persistence_dir) / BASE_STATE


async def _enter_io_window(conv: LocalConversation, llm: ControlledLLM):
    """Start arun() and wait until the LLM call has begun (lock released).

    Returns the arun task. The caller is now inside the released-lock window.
    """
    task = asyncio.create_task(conv.arun())
    await asyncio.wait_for(llm.entered.wait(), timeout=5.0)
    return task


# ---------------------------------------------------------------------------
# Core fix: pause during I/O window must persist immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_during_llm_call_persists_to_disk(tmp_path):
    """After pause() returns during an in-flight LLM call, base_state.json
    on disk must immediately show execution_status=paused (#5626)."""
    llm = ControlledLLM()
    conv = _make_conversation(llm, tmp_path)
    task = await _enter_io_window(conv, llm)

    conv.pause()

    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "paused", (
        f"base_state.json shows '{disk_state['execution_status']}', expected 'paused'."
    )

    llm.release.set()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ---------------------------------------------------------------------------
# Normal path: no concurrent pause, batching still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_arun_completes_and_persists_final_state(tmp_path):
    """Normal arun() must finish and flush FINISHED to disk.

    Verifies the stash/restore does not break the outer transaction's final flush.
    """
    conv = _make_conversation(InstantLLM(), tmp_path)
    await conv.arun()

    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "finished"
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED
    assert conv._state._save_depth == 0
    assert not conv._state._dirty


# ---------------------------------------------------------------------------
# Depth restoration: after reacquire, _save_depth is exactly restored
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depth_restored_after_io_window(tmp_path):
    """When the LLM returns, _save_depth must be restored to its pre-IO value.

    We use a ControlledLLM that releases after we verify depth is 0 (stashed).
    After arun() completes, depth must be 0 and dirty must be False.
    """
    llm = ControlledLLM()
    conv = _make_conversation(llm, tmp_path)
    task = await _enter_io_window(conv, llm)

    # While the lock is released, _save_depth has been set to 0 by the fix.
    conv.pause()  # pause's with-block flushes then exits; depth stays 0.
    assert conv._state._save_depth == 0, (
        f"During I/O window, _save_depth should be 0, got {conv._state._save_depth}"
    )

    llm.release.set()
    await asyncio.wait_for(task, timeout=5.0)

    # After arun completes, depth must be 0 (outer with-block exited cleanly)
    assert conv._state._save_depth == 0
    assert not conv._state._dirty


# ---------------------------------------------------------------------------
# Cancellation: task.cancel() during I/O window must restore depth/lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_during_io_window_restores_invariants(tmp_path):
    """Cancelling the arun task while the LLM call is in flight must:
    - persist the pause that happened during the window
    - leave _save_depth == 0
    - leave no dirty state
    """
    llm = ControlledLLM()
    conv = _make_conversation(llm, tmp_path)
    task = await _enter_io_window(conv, llm)

    conv.pause()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "paused"
    assert conv._state._save_depth == 0, (
        f"After cancel, _save_depth should be 0, got {conv._state._save_depth}"
    )


# ---------------------------------------------------------------------------
# LLM exception: error path must restore depth/lock invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_exception_restores_invariants(tmp_path):
    """If the LLM raises while the lock is released, the finally block must
    restore _save_depth and the outer transaction must handle the error.
    """
    llm = ControlledLLM(should_raise=True)
    conv = _make_conversation(llm, tmp_path)
    task = await _enter_io_window(conv, llm)

    llm.release.set()
    with pytest.raises(Exception):
        await asyncio.wait_for(task, timeout=5.0)

    # After an LLM error, the finally block must have restored _save_depth.
    assert conv._state._save_depth == 0, (
        f"After LLM exception, _save_depth should be 0, got {conv._state._save_depth}"
    )


# ---------------------------------------------------------------------------
# Concurrent mutation during I/O window: another legitimate state change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_mutation_during_io_window_persists(tmp_path):
    """A state mutation during the I/O window must produce a complete,
    valid ConversationState snapshot on disk (not a partial write).

    We pause() during the window and verify the flushed JSON contains all
    required top-level fields (id, execution_status, agent, etc.), proving
    the eager flush wrote a full consistent model dump.
    """
    llm = ControlledLLM()
    conv = _make_conversation(llm, tmp_path)
    task = await _enter_io_window(conv, llm)

    conv.pause()

    # Verify pause persisted
    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "paused"
    # Validate the JSON is a complete ConversationState (has required fields)
    assert "id" in disk_state
    assert "execution_status" in disk_state
    assert "agent" in disk_state

    llm.release.set()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
