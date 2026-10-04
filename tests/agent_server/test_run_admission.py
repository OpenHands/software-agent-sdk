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
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.testing import TestLLM
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.scripts import start_conversation_with_test_llm


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


async def test_rearm_resumes_parked_input_despite_competing_claim(runs_one):
    """A run re-armed for input parked while its predecessor was wrapping up
    must actually resume, even when a competing caller tries to claim capacity
    in that window.

    ``_run_and_publish`` re-arms after its own run yields. Without holding the
    conversation's session permit across the re-arm, the freed token could be
    taken first and the parked message would be stranded (``send_message(run=True)``
    already returned 200, so the client has no signal to retry). The session
    permit is shared with the re-arm, so the parked input always runs.
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

    # From the status read the re-arm performs after its own run yields --
    # exactly the window in which a fresh permit would look free -- try to
    # claim capacity as a competing caller would. The conversation's session
    # permit is still held, so the claim cannot succeed.
    claim_attempted = False
    original_get_status = service._get_execution_status

    async def get_status_with_competing_claim():
        nonlocal claim_attempted
        if not claim_attempted and service._run_task is None:
            claim_attempted = True
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    runs_one.owner._run_semaphore.acquire(), timeout=0.25
                )
        return await original_get_status()

    service._get_execution_status = get_status_with_competing_claim

    run_task = service._run_task
    assert run_task is not None
    runs_one.release.set()
    await run_task
    assert claim_attempted, "the competing claim window was never observed"

    # The parked message resumed off the conversation's own session permit, so
    # the competing claim could not pre-empt it.
    await wait_until(lambda: runs_one.tracker.entered >= 2)
    assert service.get_conversation().state.last_user_message_id is not None

    runs_one.release.set()
    await service.wait_for_run_completion(5)
    await wait_until(lambda: runs_one.tracker.active == 0)
    # Once the chain settles the permit is returned to the shared pool.
    await asyncio.wait_for(runs_one.owner._run_semaphore.acquire(), timeout=5)
    runs_one.owner._run_semaphore.release()


async def test_terminal_status_implies_released_permit(tmp_path, monkeypatch):
    """A run that is observable as terminal must already have returned its permit.

    Regression: ``_maybe_end_run_session`` asked "is a successor run active?"
    via ``self._run_task is not None and not self._run_task.done()``. The run
    body calls it from its *own* ``finally``, so the task was always its own
    "successor" and the release was deferred to the task's done callback --
    after ``wait_for_pending`` and the final state publish. A conversation that
    already reported FINISHED therefore still occupied a run slot, so a caller
    that sized capacity exactly to its workload (the concurrent-conversations
    benchmark: 16 conversations, max_concurrent_runs=16) got a spurious
    ConversationRunLimitExceeded.

    Holding the run in its ``wait_for_pending`` tail makes the window
    deterministic: status is terminal, so the slot must be free.
    """
    tail_entered = threading.Event()
    tail_release = threading.Event()

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        max_concurrent_runs=1,
    ) as owner:
        llm = TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="done")])]
        )
        info = await start_conversation_with_test_llm(
            owner,
            parent_llm=llm,
            workspace_dir=str(tmp_path / "ws"),
            usage_id="admission-terminal",
        )
        service = await owner.get_event_service(info.id)
        assert service is not None
        wrapper = service._callback_wrapper
        assert wrapper is not None, "expected an async callback wrapper"

        original_wait_for_pending = wrapper.wait_for_pending

        def blocking_wait_for_pending(timeout: float | None = None) -> None:
            tail_entered.set()
            assert tail_release.wait(timeout=10), "tail was never released"
            original_wait_for_pending(timeout)

        monkeypatch.setattr(wrapper, "wait_for_pending", blocking_wait_for_pending)

        await service.run()
        assert await asyncio.to_thread(tail_entered.wait, 10), (
            "run never reached its wait_for_pending tail"
        )

        # The run is terminal from the client's point of view...
        status = await service.get_state()
        assert status.execution_status == ConversationExecutionStatus.FINISHED, (
            f"expected FINISHED in the tail, got {status.execution_status}"
        )
        # ...so it must not still be holding the only permit.
        semaphore = owner._run_semaphore
        assert semaphore is not None
        assert semaphore._value == 1, (
            "conversation reported terminal status while still holding its run "
            "permit; a caller that sized capacity to its workload would be "
            "refused with a spurious ConversationRunLimitExceeded"
        )

        tail_release.set()
        assert (
            await service.wait_for_run_completion(10)
            == ConversationExecutionStatus.FINISHED
        )
