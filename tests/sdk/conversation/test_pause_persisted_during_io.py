"""Tests for #5626: pause during in-flight LLM call must persist to base_state.json.

When the run loop releases the state lock for an awaited LLM network call,
a concurrent pause() must immediately persist the new execution_status to
disk (base_state.json), not defer it until the step ends.
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


class HangingLLM(LLM):
    """LLM that hangs in acompletion so we can pause mid-call."""

    def __init__(self):
        super().__init__(model="test-hang", usage_id="test-hang")

    async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
        await asyncio.sleep(30.0)
        return _make_response("test-hang")


class InstantLLM(LLM):
    """LLM that completes immediately."""

    def __init__(self):
        super().__init__(model="test-instant", usage_id="test-instant")

    async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
        return _make_response("test-instant")


class FlakyLLM(LLM):
    """LLM that raises on first call, succeeds on second."""

    _call_count: int = PrivateAttr(default=0)

    def __init__(self):
        super().__init__(model="test-flaky", usage_id="test-flaky")

    async def acompletion(self, messages, tools=None, **kw) -> LLMResponse:  # type: ignore[override]
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError("LLM transient error")
        return _make_response("test-flaky")


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
    """Locate base_state.json on disk for this conversation."""
    return Path(conv._state.persistence_dir) / BASE_STATE


@pytest.mark.asyncio
async def test_pause_during_llm_call_persists_to_disk(tmp_path):
    """After pause() returns during an in-flight LLM call, base_state.json
    on disk must immediately show execution_status=paused (#5626)."""
    conv = _make_conversation(HangingLLM(), tmp_path)

    task = asyncio.create_task(conv.arun())

    # Wait for arun() to enter the LLM call (which releases the state lock
    # via _released_state_lock_during_io).
    await asyncio.sleep(0.3)

    # Call pause() — this acquires the now-free state lock and sets PAUSED.
    conv.pause()

    # Read base_state.json from disk. On current main, this still says
    # "running" because _save_depth is 1 (deferred save). After the fix,
    # it should say "paused".
    bs_path = _base_state_path(conv)
    assert bs_path.exists(), f"base_state.json not found at {bs_path}"
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "paused", (
        f"base_state.json shows '{disk_state['execution_status']}', "
        f"expected 'paused'. The pause was not persisted during the I/O window."
    )

    # Clean up: cancel the hanging task
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_normal_arun_completes_and_persists_final_state(tmp_path):
    """Normal (no concurrent pause) arun() must complete and persist final state.

    Verifies that the _save_depth stash/restore in _released_state_lock_during_io
    does not break the normal batching path: after the LLM returns, depth is
    restored and the outer with-block exits correctly, flushing FINISHED.
    """
    conv = _make_conversation(InstantLLM(), tmp_path)
    await conv.arun()

    # Final state on disk must be FINISHED
    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "finished", (
        f"Normal arun left disk state as '{disk_state['execution_status']}', "
        f"expected 'finished'."
    )
    # In-memory state agrees
    assert conv.state.execution_status == ConversationExecutionStatus.FINISHED


@pytest.mark.asyncio
async def test_save_depth_restored_after_io_window(tmp_path):
    """After the LLM call returns and the lock is reacquired, _save_depth must
    be restored to its pre-IO value so the outer transaction continues batching.

    We verify this by checking that after arun() completes normally, the state
    is consistent and _save_depth is back to 0 (not negative, not stuck >0).
    """
    conv = _make_conversation(InstantLLM(), tmp_path)
    await conv.arun()

    # After arun completes, _save_depth should be 0 (all with-blocks exited)
    assert conv._state._save_depth == 0, (
        f"After arun, _save_depth should be 0, got {conv._state._save_depth}"
    )
    assert not conv._state._dirty, (
        "After arun, state should not be dirty (all saves flushed)"
    )


@pytest.mark.asyncio
async def test_interrupt_still_works_after_fix(tmp_path):
    """interrupt() (task cancellation) must still work after the fix.

    Verifies that the finally block in _released_state_lock_during_io correctly
    restores _save_depth even when the task is cancelled mid-LLM-call.
    """
    conv = _make_conversation(HangingLLM(), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.3)

    conv.interrupt()
    await asyncio.wait_for(task, timeout=2.0)

    assert conv.state.execution_status == ConversationExecutionStatus.PAUSED
    # After interrupt + arun unwinding, _save_depth must be 0
    assert conv._state._save_depth == 0, (
        f"After interrupt, _save_depth should be 0, got {conv._state._save_depth}"
    )


@pytest.mark.asyncio
async def test_pause_persists_and_saves_depth_is_zero(tmp_path):
    """After pause during an in-flight LLM call and task cancellation,
    _save_depth must be 0 (not leaked) and disk shows paused.

    Verifies the finally block in _released_state_lock_during_io restores
    state invariants even when the task is cancelled mid-LLM-call.
    """
    conv = _make_conversation(HangingLLM(), tmp_path)

    task = asyncio.create_task(conv.arun())
    await asyncio.sleep(0.3)
    conv.pause()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # Disk must show paused (the core #5626 fix)
    bs_path = _base_state_path(conv)
    disk_state = json.loads(bs_path.read_text())
    assert disk_state["execution_status"] == "paused"

    # _save_depth must be 0 — the finally block restored it
    assert conv._state._save_depth == 0, (
        f"After cancel, _save_depth should be 0, got {conv._state._save_depth}"
    )
