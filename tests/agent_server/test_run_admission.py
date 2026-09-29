"""Concurrency is counted across native async and thread-backed agent runs."""

import asyncio
import threading
from contextlib import asynccontextmanager, suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.event_service import ConversationRunLimitExceeded
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.workspace import LocalWorkspace


async def wait_until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def runs(tmp_path, monkeypatch):
    async with _runs(tmp_path, monkeypatch, max_concurrent_runs=2) as runs_fixture:
        yield runs_fixture


@pytest_asyncio.fixture
async def runs_one(tmp_path, monkeypatch):
    async with _runs(tmp_path, monkeypatch, max_concurrent_runs=1) as runs_fixture:
        yield runs_fixture


@asynccontextmanager
async def _runs(tmp_path, monkeypatch, max_concurrent_runs):
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
        conversations_dir=tmp_path / "conversations",
        max_concurrent_runs=max_concurrent_runs,
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
            yield SimpleNamespace(
                create=create, tracker=tracker, release=release, owner=owner
            )
        finally:
            release.set()
            await asyncio.gather(
                *(s.wait_for_run_completion(5) for s in services if s.is_open())
            )


@pytest.mark.parametrize(
    "modes", [("async",) * 5, ("sync",) * 5, ("sync", "async") * 3]
)
async def test_shared_limit_rejects_burst_and_reuses_capacity(runs, modes):
    services = [await runs.create(mode) for mode in modes]
    results = await asyncio.gather(*(s.run() for s in services), return_exceptions=True)
    assert sum(r is None for r in results) == 2
    assert (
        sum(isinstance(r, ConversationRunLimitExceeded) for r in results)
        == len(modes) - 2
    )
    await wait_until(lambda: runs.tracker.active == 2)
    rejected = [s for s, r in zip(services, results) if r is not None]
    assert all(s._run_task is None for s in rejected)
    runs.release.set()
    await asyncio.gather(*(s.wait_for_run_completion(5) for s in services))
    for service in rejected:
        await service.run()
        await service.wait_for_run_completion(5)
    assert runs.tracker.peak == 2
    assert runs.tracker.entered == len(modes)


@pytest.mark.parametrize("mode", ["async", "sync"])
async def test_run_errors_release_capacity(runs, mode):
    services = [await runs.create(mode, fail=i < 2) for i in range(3)]
    for service in services[:2]:
        await service.run()
    runs.release.set()
    await asyncio.gather(*(s.wait_for_run_completion(5) for s in services[:2]))
    await services[2].run()
    await services[2].wait_for_run_completion(5)
    assert runs.tracker.entered == 3
    assert runs.tracker.active == 0


async def test_cancelled_sync_waiter_keeps_slot_until_worker_exits(runs):
    holders = [await runs.create("sync") for _ in range(2)]
    spare = await runs.create()
    for holder in holders:
        await holder.run()
    await wait_until(lambda: runs.tracker.active == 2)
    task = holders[0]._run_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    with pytest.raises(ConversationRunLimitExceeded):
        await spare.run()
    runs.release.set()
    await holders[1].wait_for_run_completion(5)
    await wait_until(lambda: runs.tracker.active == 0)
    await spare.run()
    await spare.wait_for_run_completion(5)
    assert runs.tracker.peak == 2


async def test_cancelled_async_run_releases_slot(runs):
    holders = [await runs.create() for _ in range(2)]
    spare = await runs.create()
    for holder in holders:
        await holder.run()
    await wait_until(lambda: runs.tracker.active == 2)
    task = holders[0]._run_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await spare.run()
    await wait_until(lambda: runs.tracker.entered == 3)
    assert runs.tracker.active == 2


async def test_cancel_before_background_task_starts_releases_slot(runs):
    services = [await runs.create() for _ in range(3)]
    await services[0].run()
    task = services[0]._run_task
    assert task is not None
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    for service in services[1:]:
        await service.run()
    await wait_until(lambda: runs.tracker.active == 2)
    assert runs.tracker.entered == 2


async def test_pause_before_background_task_starts_is_honored(runs, monkeypatch):
    service = await runs.create()
    monkeypatch.setattr(service, "_publish_state_update", AsyncMock())
    await service.run()
    await service.pause()
    runs.release.set()
    await service.wait_for_run_completion(5)
    assert runs.tracker.entered == 0


async def test_create_rejected_before_initialization_and_reattach_allowed(
    runs, monkeypatch
):
    holders = [await runs.create() for _ in range(2)]
    for holder in holders:
        await holder.run()
    initialize = AsyncMock(side_effect=AssertionError("must not initialize"))
    monkeypatch.setattr(runs.owner, "_create_conversation", initialize)
    with pytest.raises(ConversationRunLimitExceeded):
        await runs.create()
    initialize.assert_not_called()
    info, created = await runs.owner.start_conversation(
        StartConversationRequest(
            conversation_id=holders[0].stored.id,
            workspace=holders[0].stored.workspace,
            agent=holders[0].get_conversation().agent,
        )
    )
    assert info.id == holders[0].stored.id
    assert not created


async def test_creation_reservations_are_atomic_and_released_on_failure(
    runs, monkeypatch
):
    entered = 0
    release = asyncio.Event()

    async def initialize(*args):
        nonlocal entered
        entered += 1
        await release.wait()
        raise ValueError("initialization failed")

    monkeypatch.setattr(runs.owner, "_create_conversation", initialize)
    tasks = [asyncio.create_task(runs.create()) for _ in range(5)]
    await wait_until(lambda: entered == 2 and sum(t.done() for t in tasks) == 3)
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(isinstance(r, ConversationRunLimitExceeded) for r in results) == 3
    with pytest.raises(ValueError, match="initialization failed"):
        await runs.create()
    assert entered == 3


async def test_message_retained_but_run_rejected(runs):
    holders = [await runs.create() for _ in range(2)]
    spare = await runs.create()
    for holder in holders:
        await holder.run()
    with pytest.raises(ConversationRunLimitExceeded, match="Message saved"):
        await spare.send_message(
            Message(role="user", content=[TextContent(text="hi")]), run=True
        )
    assert spare._run_task is None
    assert spare.get_conversation().state.last_user_message_id is not None


async def test_goal_rejected_before_scheduling(runs):
    holders = [await runs.create() for _ in range(2)]
    spare = await runs.create()
    for holder in holders:
        await holder.run()
    with pytest.raises(ConversationRunLimitExceeded):
        await spare.start_goal_loop("Do something")
    assert spare._goal_loop_task is None


async def test_rearm_survives_competing_claim_of_released_permit(runs_one):
    """A request re-armed after its run finishes must not be dropped when a
    competing caller claims the just-released permit before the re-arm's
    ``run()`` can re-acquire it.

    ``_run_and_publish`` calls ``release_slot()`` before its re-arm, so the
    freed permit can be taken first. ``run()`` then raises
    ``ConversationRunLimitExceeded`` from ``RunSlot.acquire``; if the re-arm
    only handles ``ValueError`` that refusal escapes the background task and
    the pending input is stranded.
    """
    service = await runs_one.create()
    await service.run()
    await wait_until(lambda: runs_one.tracker.active == 1)

    # Deliver input while the only permit is held: run() refuses with
    # "conversation_already_running" and the message is parked as pending.
    await service.send_message(
        Message(role="user", content=[TextContent(text="stranded")]), run=True
    )
    assert service._rerun_requested is True

    # Model a competing request claiming the permit the finishing run releases,
    # before the re-arm's run() reaches RunSlot.acquire. The claim is made from
    # the status read the re-arm performs after release_slot(), exactly the
    # window in which the permit is free but the re-arm has not re-acquired it.
    claimed = False
    original_get_status = service._get_execution_status

    async def get_status_with_competing_claim():
        nonlocal claimed
        if not claimed and service._run_task is None:
            claimed = True
            await runs_one.owner._run_semaphore.acquire()
        return await original_get_status()

    service._get_execution_status = get_status_with_competing_claim

    run_task = service._run_task
    assert run_task is not None
    runs_one.release.set()
    # Pre-fix this raises ConversationRunLimitExceeded out of the background
    # task, stranding the parked message with nothing left to reschedule it.
    await run_task
    assert claimed, "the competing claim never happened"

    # The refusal is handled like "conversation_already_running": the request
    # stays pending so it is retried once capacity frees.
    assert service._rerun_requested is True
    assert service._run_task is None

    # Capacity frees and the caller retries /run, as the 429 response
    # instructs. The parked message must now get a run.
    runs_one.owner._run_semaphore.release()
    await service.run()
    await wait_until(lambda: runs_one.tracker.entered >= 2)
    runs_one.release.set()
    await service.wait_for_run_completion(5)
    await wait_until(lambda: runs_one.tracker.active == 0)
    assert service.get_conversation().state.last_user_message_id is not None
