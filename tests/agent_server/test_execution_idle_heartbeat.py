"""Active execution keeps idle status fresh and excludes an idle-pause claim."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.agent_server import event_service, server_details_router as details


@pytest.fixture(autouse=True)
def reset_execution_state(monkeypatch):
    monkeypatch.setattr(details, "_active_executions", 0)
    monkeypatch.setattr(details, "_idle_pause_fenced", False)
    monkeypatch.setattr(details, "_last_event_time", time.time() - 1201)
    yield
    assert details._active_executions == 0


def test_execution_and_pause_claim_are_mutually_exclusive(monkeypatch):
    assert details.begin_execution()
    # Even after an arbitrarily long quiet LLM call, active execution excludes pause.
    monkeypatch.setattr(details, "_last_event_time", time.time() - 1201)
    result = details.claim_idle_pause(1200)
    assert not result.claimed
    assert result.active_executions == 1
    details.finish_execution()
    assert not details.claim_idle_pause(1200).claimed
    monkeypatch.setattr(details, "_last_event_time", time.time() - 1201)
    assert details.claim_idle_pause(1200).claimed
    assert not details.claim_idle_pause(1200).claimed
    assert not details.begin_execution()
    details.release_idle_pause()
    assert details.begin_execution()
    details.finish_execution()


@pytest.mark.asyncio
@pytest.mark.parametrize("goal_loop", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_heartbeat_lifetime_matches_run_and_goal_loop(
    monkeypatch, goal_loop, cancel
):
    started = asyncio.Event()
    release = asyncio.Event()
    third_heartbeat = asyncio.Event()
    calls = 0

    async def execute(*args, **kwargs):
        started.set()
        await release.wait()

    def record_activity():
        nonlocal calls
        calls += 1
        if calls == 3:
            third_heartbeat.set()

    monkeypatch.setattr(event_service, "EXECUTION_ACTIVITY_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(details, "update_last_execution_time", record_activity)
    service = object.__new__(event_service.EventService)
    if goal_loop:
        monkeypatch.setattr(service, "_run_goal_loop", execute)
        task = service._create_goal_loop_task(MagicMock())
    else:

        class BlockingAgent:
            async def astep(self):
                pass

        class BlockingConversation:
            agent = BlockingAgent()

            async def arun(self):
                await execute()

        monkeypatch.setattr(
            service, "_conversation", BlockingConversation(), raising=False
        )
        service._closing = False
        service._run_lock = asyncio.Lock()
        service._run_task = None
        service._callback_wrapper = None
        service._rerun_requested = False
        service._acp_internal_rerun_requested = False
        service._explicit_interrupt_generation = 0
        monkeypatch.setattr(
            service,
            "_get_execution_status",
            AsyncMock(return_value=event_service.ConversationExecutionStatus.IDLE),
        )
        monkeypatch.setattr(service, "_publish_state_update", AsyncMock())
        await service.run()
        task = service._run_task
        assert task is not None
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(third_heartbeat.wait(), 1)
        assert details._active_executions == 1
        assert not details.claim_idle_pause(1200).claimed
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await asyncio.wait_for(task, 1)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
    assert details._active_executions == 0
    completed_calls = calls
    await asyncio.sleep(0.01)
    assert calls == completed_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_fenced_runtime_rejects_goal_loop_start_and_resume(monkeypatch, resume):
    assert details.claim_idle_pause(1200).claimed
    service = object.__new__(event_service.EventService)
    execute = AsyncMock()
    monkeypatch.setattr(service, "_run_goal_loop", execute)
    with pytest.raises(ValueError, match="runtime_idle_pause_in_progress"):
        service._create_goal_loop_task(MagicMock(), resume=resume)
    execute.assert_not_awaited()
