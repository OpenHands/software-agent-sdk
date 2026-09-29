"""Concurrency is counted across native async and thread-backed agent runs."""

import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def runs(tmp_path, monkeypatch):
    tracker = SimpleNamespace(active=0, peak=0, entered=0)
    release = threading.Event()
    lock = threading.Lock()
    services = []

    def enter():
        with lock:
            tracker.active += 1
            tracker.entered += 1
            tracker.peak = max(tracker.peak, tracker.active)

    def leave():
        with lock:
            tracker.active -= 1

    async with ConversationService(
        conversations_dir=tmp_path / "conversations", max_concurrent_runs=2
    ) as owner:

        async def create(mode="async", fail=False):
            info, _ = await owner.start_conversation(
                StartConversationRequest(
                    agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
                    workspace=LocalWorkspace(working_dir=str(tmp_path)),
                    autotitle=False,
                )
            )
            service = await owner.get_event_service(info.id)
            assert service is not None and service._conversation is not None
            conversation = service._conversation

            async def arun():
                enter()
                try:
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                    if fail:
                        raise RuntimeError("test run failed")
                finally:
                    leave()

            def run():
                enter()
                try:
                    assert release.wait(timeout=10)
                    if fail:
                        raise RuntimeError("test run failed")
                finally:
                    leave()

            monkeypatch.setattr(conversation, "run", run)
            monkeypatch.setattr(conversation, "arun", arun if mode == "async" else None)
            services.append(service)
            return service

        try:
            yield SimpleNamespace(create=create, tracker=tracker, release=release)
        finally:
            release.set()
            await asyncio.gather(
                *(s.wait_for_run_completion(5) for s in services if s.is_open())
            )


@pytest.mark.parametrize(
    "modes", [("async",) * 5, ("sync",) * 5, ("sync", "async") * 3]
)
async def test_shared_limit_and_capacity_reuse(runs, modes):
    services = [await runs.create(mode) for mode in modes]
    for service in services:
        await service.run()
    await wait_until(lambda: runs.tracker.entered >= 2)
    await asyncio.sleep(0.05)
    assert runs.tracker.active == 2
    runs.release.set()
    await asyncio.gather(
        *(s.wait_for_run_completion(5) for s in services if s.is_open())
    )
    assert runs.tracker.peak == 2
    assert runs.tracker.entered == len(modes)
    assert runs.tracker.active == 0


@pytest.mark.parametrize("operation", ["pause", "interrupt", "close"])
async def test_stopped_queued_run_does_not_start(runs, operation):
    holders = [await runs.create() for _ in range(2)]
    queued = await runs.create()
    for holder in holders:
        await holder.run()
    await wait_until(lambda: runs.tracker.active == 2)
    await queued.run()
    # Exercise the real lifecycle method, including a stop before the task's
    # first turn. Do not forge the interrupt-generation counter.
    stop = asyncio.create_task(getattr(queued, operation)())
    await wait_until(
        lambda: queued._closing
        if operation == "close"
        else queued._explicit_interrupt_generation > 0
    )
    runs.release.set()
    await stop
    await asyncio.gather(*(s.wait_for_run_completion(5) for s in holders))
    if operation != "close":
        await queued.wait_for_run_completion(5)
    assert runs.tracker.entered == 2


@pytest.mark.parametrize("mode", ["async", "sync"])
async def test_run_errors_release_capacity(runs, mode):
    services = [await runs.create(mode, fail=i < 2) for i in range(3)]
    for service in services:
        await service.run()
    runs.release.set()
    await asyncio.gather(
        *(s.wait_for_run_completion(5) for s in services if s.is_open())
    )
    assert runs.tracker.entered == 3
    assert runs.tracker.active == 0


async def test_cancelled_sync_waiter_keeps_slot_until_worker_exits(runs):
    holders = [await runs.create("sync") for _ in range(2)]
    for holder in holders:
        await holder.run()
    await wait_until(lambda: runs.tracker.active == 2)
    task = holders[0]._run_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    queued = await runs.create()
    await queued.run()
    await asyncio.sleep(0.05)
    assert runs.tracker.entered == 2, (
        "cancelled waiter released a still-running thread's slot"
    )
    runs.release.set()
    await queued.wait_for_run_completion(5)
    assert runs.tracker.peak == 2


async def test_cancelled_async_run_releases_slot(runs):
    holders = [await runs.create() for _ in range(2)]
    for holder in holders:
        await holder.run()
    await wait_until(lambda: runs.tracker.active == 2)
    task = holders[0]._run_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    queued = await runs.create()
    await queued.run()
    await wait_until(lambda: runs.tracker.entered == 3)
    assert runs.tracker.active == 2


async def test_pause_before_background_task_starts_is_honored(runs, monkeypatch):
    service = await runs.create()
    # Force pause() to complete before the scheduled task gets its first turn.
    monkeypatch.setattr(service, "_publish_state_update", AsyncMock())
    await service.run()
    await service.pause()
    runs.release.set()
    await service.wait_for_run_completion(5)
    assert runs.tracker.entered == 0
