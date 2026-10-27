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
from openhands.agent_server.openai.service import _run_agent
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.terminal import TerminalObservation
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


async def test_gateway_turn_refused_before_message_is_saved(runs, tmp_path):
    holders = [await runs.create() for _ in range(2)]
    spare = await runs.create()
    for holder in holders:
        await holder.run()
    events_before = len((await spare.get_state()).events)
    for _ in range(2):  # an OpenAI client retries a 429 with the same request
        with pytest.raises(
            ConversationRunLimitExceeded, match="Retry the request later"
        ):
            await _run_agent(
                start_request=StartConversationRequest(
                    agent=Agent(llm=LLM(model="gpt-4o", usage_id="test"), tools=[]),
                    workspace=LocalWorkspace(working_dir=str(tmp_path)),
                ),
                user_content=[TextContent(text="hi")],
                conversation_service=runs.owner,
                reusable_conversation_id=spare.stored.id,
            )
    assert spare._run_task is None
    assert len((await spare.get_state()).events) == events_before
    assert spare.get_conversation().state.last_user_message_id is None
    runs.release.set()
    await asyncio.gather(*(h.wait_for_run_completion(5) for h in holders))
    slot = await spare.reserve_run_slot()
    assert slot is not None
    await spare.send_message(
        Message(role="user", content=[TextContent(text="hi")]),
        run=True,
        run_slot=slot,
    )
    slot.release()
    await spare.wait_for_run_completion(5)
    assert runs.tracker.entered == 3
    assert runs.tracker.active == 0


async def test_reservation_skipped_while_conversation_holds_its_permit(runs):
    holders = [await runs.create() for _ in range(2)]
    for holder in holders:
        await holder.run()
    assert await holders[0].reserve_run_slot() is None


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


@asynccontextmanager
async def _direct_capacity(tmp_path, monkeypatch, max_concurrent_runs):
    """Conversations whose direct tool call and run both block on one event.

    Both admission paths draw on the shared ``_run_semaphore``, so the same
    release event lets a test hold capacity on either side and observe what the
    other side is refused.
    """
    state = SimpleNamespace(active=0, peak=0, entered=0)
    lock = threading.Lock()
    release = threading.Event()
    services = []

    def enter():
        with lock:
            state.active += 1
            state.entered += 1
            state.peak = max(state.peak, state.active)

    def leave():
        with lock:
            state.active -= 1

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        max_concurrent_runs=max_concurrent_runs,
    ) as owner:

        async def create():
            info, _ = await owner.start_conversation(
                StartConversationRequest(
                    agent=Agent(
                        llm=LLM(model="gpt-4o", usage_id="test"),
                        tools=[Tool(name="terminal")],
                    ),
                    workspace=LocalWorkspace(working_dir=str(tmp_path)),
                    autotitle=False,
                )
            )
            service = await owner.get_event_service(info.id)
            assert service is not None and service._conversation is not None
            conversation = service._conversation

            def execute_tool(tool_name, action):
                enter()
                try:
                    assert release.wait(timeout=30)
                    return TerminalObservation.from_text("done", command="x")
                finally:
                    leave()

            async def arun():
                enter()
                try:
                    while not release.is_set():
                        await asyncio.sleep(0.01)
                finally:
                    leave()

            conversation.execute_tool = execute_tool  # type: ignore[method-assign]
            monkeypatch.setattr(conversation, "arun", arun)
            services.append(service)
            return service

        try:
            yield SimpleNamespace(create=create, state=state, release=release)
        finally:
            release.set()
            await asyncio.gather(
                *(s.wait_for_run_completion(5) for s in services if s.is_open())
            )


async def test_direct_tool_calls_share_the_server_run_limit(tmp_path, monkeypatch):
    """Direct calls from many conversations count against one server budget.

    Each conversation serializes its own direct calls, but nothing bounds the
    total across conversations: without a shared permit, every conversation can
    submit a direct call at once and queue unbounded work in the process
    default executor, bypassing ``max_concurrent_runs``.
    """
    async with _direct_capacity(tmp_path, monkeypatch, 2) as capacity:
        services = [await capacity.create() for _ in range(4)]
        tasks = [
            asyncio.create_task(
                service.execute_tool(
                    "terminal", {"kind": "TerminalAction", "command": "x"}
                )
            )
            for service in services
        ]
        # The two admitted calls stay blocked in the tool; the other two must be
        # refused rather than queueing in the executor.
        await wait_until(
            lambda: sum(t.done() for t in tasks) == 2 and capacity.state.active == 2
        )
        refused = [t for t in tasks if t.done()]
        assert all(
            isinstance(t.exception(), ConversationRunLimitExceeded) for t in refused
        )
        capacity.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert capacity.state.peak == 2


async def test_direct_tool_call_competes_with_a_run_for_capacity(tmp_path, monkeypatch):
    """A direct call and a run draw on the same server-wide budget.

    Holding the only permit on a direct call must refuse a run in another
    conversation (429), and releasing the direct call must free the permit for
    that run.
    """
    async with _direct_capacity(tmp_path, monkeypatch, 1) as capacity:
        holder = await capacity.create()
        other = await capacity.create()

        direct = asyncio.create_task(
            holder.execute_tool("terminal", {"kind": "TerminalAction", "command": "x"})
        )
        await wait_until(lambda: capacity.state.active == 1)

        with pytest.raises(ConversationRunLimitExceeded):
            await other.run()

        capacity.release.set()
        await direct
        await other.run()
        await wait_until(lambda: capacity.state.entered == 2)


async def test_direct_call_keeps_its_permit_until_close_drains_it(
    tmp_path, monkeypatch
):
    """A direct call being closed still occupies its server permit.

    Regression: ``EventService.close()`` returned the permit before it waited
    for the abandoned worker and its offloaded interrupt. With
    ``max_concurrent_runs=1`` that freed the only slot while the closing worker
    was still running, so a competing call in another conversation was admitted
    alongside live work and the server exceeded its own budget. The permit must
    be held until both settle.
    """
    async with _direct_capacity(tmp_path, monkeypatch, 1) as capacity:
        holder = await capacity.create()
        other = await capacity.create()

        direct = asyncio.create_task(
            holder.execute_tool("terminal", {"kind": "TerminalAction", "command": "x"})
        )
        await wait_until(lambda: capacity.state.active == 1)

        close_task = asyncio.create_task(holder.close())
        await asyncio.sleep(0.2)
        assert close_task.done() is False

        # The closing conversation still holds the only permit: a competing
        # call in another conversation must be refused, not admitted next to
        # the worker close() is still draining.
        with pytest.raises(ConversationRunLimitExceeded):
            await other.execute_tool(
                "terminal", {"kind": "TerminalAction", "command": "y"}
            )

        capacity.release.set()
        await asyncio.wait_for(close_task, timeout=10)
        await asyncio.gather(direct, return_exceptions=True)

        # Once close() has drained the worker the permit is returned, so the
        # same call is admitted: the 429 was capacity, not a permanent refusal.
        await other.execute_tool("terminal", {"kind": "TerminalAction", "command": "y"})
        assert capacity.state.entered == 2


async def _terminal_services(tmp_path, max_concurrent_runs):
    """A ``ConversationService`` owner plus a factory for terminal conversations."""
    owner = ConversationService(
        conversations_dir=tmp_path / "conversations",
        max_concurrent_runs=max_concurrent_runs,
    )

    async def create():
        info, _ = await owner.start_conversation(
            StartConversationRequest(
                agent=Agent(
                    llm=LLM(model="gpt-4o", usage_id="test"),
                    tools=[Tool(name="terminal")],
                ),
                workspace=LocalWorkspace(working_dir=str(tmp_path)),
                autotitle=False,
            )
        )
        service = await owner.get_event_service(info.id)
        assert service is not None and service._conversation is not None
        return service

    return owner, create


def _free_run_permits(service) -> int:
    """Permits still available on the conversation's shared run semaphore."""
    semaphore = service._run_semaphore
    assert semaphore is not None
    return semaphore._value


async def test_cancelled_resolve_keeps_its_permit_until_initialization_finishes(
    tmp_path, monkeypatch
):
    """A direct call cancelled while resolving the tool still holds its permit.

    Regression: ``_end_direct_tool_sync`` released the permit on the
    ``worker is None`` cancellation branch, but the resolve future is shielded,
    so ``_ensure_agent_ready`` kept initializing plugins and tools on its
    thread. With ``max_concurrent_runs=1`` a competing call in another
    conversation was admitted next to that live initialization. The permit must
    be held until the resolve future actually finishes.
    """
    owner, create = await _terminal_services(tmp_path, 1)
    async with owner:
        holder = await create()
        other = await create()

        resolve_started = threading.Event()
        resolve_release = threading.Event()
        real_resolve = holder._resolve_direct_tool

        def blocked_resolve(tool_name):
            resolve_started.set()
            resolve_release.wait(timeout=30)
            return real_resolve(tool_name)

        monkeypatch.setattr(holder, "_resolve_direct_tool", blocked_resolve)

        direct = asyncio.create_task(
            holder.execute_tool("terminal", {"kind": "TerminalAction", "command": "x"})
        )
        await asyncio.to_thread(resolve_started.wait, 10)
        direct.cancel()
        with pytest.raises(asyncio.CancelledError):
            await direct

        # The resolve thread is still blocked in initialization, so the only
        # permit must stay taken.
        with pytest.raises(ConversationRunLimitExceeded):
            await other.execute_tool(
                "terminal", {"kind": "TerminalAction", "command": "y"}
            )

        resolve_release.set()
        await wait_until(lambda: _free_run_permits(holder) == 1)

        # The permit came back once initialization finished, so the same call is
        # admitted rather than stranded.
        await other.execute_tool("terminal", {"kind": "TerminalAction", "command": "y"})


async def test_cancelled_close_keeps_its_permit_until_the_worker_exits(
    tmp_path, monkeypatch
):
    """Cancelling close() must not return the permit under a live worker.

    Regression: a cancellation at close()'s worker wait was swallowed, so
    close() continued into ``_release_direct_tool_slot()`` while the worker was
    still running. With ``max_concurrent_runs=1`` a competing call in another
    conversation was admitted alongside that live work. Cancellation must abort
    the teardown and leave the release to the worker's done callback.
    """
    owner, create = await _terminal_services(tmp_path, 1)
    async with owner:
        holder = await create()
        other = await create()
        conversation = holder.get_conversation()
        conversation._ensure_agent_ready()

        worker_started = threading.Event()
        worker_release = threading.Event()

        def blocked_execute_tool(tool_name, action):
            worker_started.set()
            worker_release.wait(timeout=30)
            return TerminalObservation.from_text("done", command="x")

        monkeypatch.setattr(conversation, "execute_tool", blocked_execute_tool)

        direct = asyncio.create_task(
            holder.execute_tool("terminal", {"kind": "TerminalAction", "command": "x"})
        )
        await asyncio.to_thread(worker_started.wait, 10)

        close_task = asyncio.create_task(holder.close())
        await asyncio.sleep(0.2)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task

        # The worker is still blocked, so the permit must not have been returned
        # by the cancelled close().
        with pytest.raises(ConversationRunLimitExceeded):
            await other.execute_tool(
                "terminal", {"kind": "TerminalAction", "command": "y"}
            )

        worker_release.set()
        await asyncio.gather(direct, return_exceptions=True)
        await wait_until(lambda: _free_run_permits(holder) == 1)

        await other.execute_tool("terminal", {"kind": "TerminalAction", "command": "y"})


async def test_cancelled_close_still_tears_the_conversation_down(tmp_path, monkeypatch):
    """Cancelling close() must not strand the conversation mid-teardown.

    Regression: a cancellation at close()'s worker wait re-raised out of
    ``close()``, so the rest of teardown never ran. The completion callbacks
    returned the run permit, but the conversation, its executor and its lease
    stayed allocated, and ``_closing`` blocked idle eviction, so nothing could
    release them. Teardown must be owned by the service and finish even though
    the caller is gone.
    """
    owner, create = await _terminal_services(tmp_path, 1)
    async with owner:
        holder = await create()
        conversation = holder.get_conversation()
        conversation._ensure_agent_ready()

        worker_started = threading.Event()
        worker_release = threading.Event()

        def blocked_execute_tool(tool_name, action):
            worker_started.set()
            worker_release.wait(timeout=30)
            return TerminalObservation.from_text("done", command="x")

        monkeypatch.setattr(conversation, "execute_tool", blocked_execute_tool)

        direct = asyncio.create_task(
            holder.execute_tool("terminal", {"kind": "TerminalAction", "command": "x"})
        )
        await asyncio.to_thread(worker_started.wait, 10)

        close_task = asyncio.create_task(holder.close())
        await asyncio.sleep(0.2)
        close_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await close_task

        # The worker is still live, so teardown is still pending.
        assert holder.is_open()

        worker_release.set()
        await asyncio.gather(direct, return_exceptions=True)
        # Teardown finishes on its own once the worker settles: the
        # conversation and its executor are closed and the lease is released.
        await wait_until(lambda: not holder.is_open())
        await wait_until(lambda: _free_run_permits(holder) == 1)
        assert holder._conversation is None
        assert holder._lease is None
        assert holder._lease_generation is None
