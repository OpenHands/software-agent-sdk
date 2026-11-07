"""A re-arm that waits for run capacity must not outlive an explicit stop.

Interleaving (capacity 1, conversations A and B):

1. A's run finishes and parks in its ``wait_for_pending`` tail. ``settle()``
   already returned A's permit.
2. B claims the only permit.
3. ``A.send_message(run=True)`` is refused as "conversation_already_running"
   (A's task is still set) and parks the message: ``_rerun_requested``.
4. A's tail ends and re-arms with ``run(wait_for_capacity=True)``, which passes
   its stop checks and then blocks on the semaphore.
5. The user pauses (or deletes) A.
6. B finishes. A's re-arm acquires the permit and must not start a run.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import PauseEvent
from openhands.sdk.workspace import LocalWorkspace


async def _wait_until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def _setup(owner, tmp_path, monkeypatch):
    async def create(name):
        info, _ = await owner.start_conversation(
            StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id=name), tools=[]),
                workspace=LocalWorkspace(working_dir=str(tmp_path)),
                autotitle=False,
            )
        )
        service = await owner.get_event_service(info.id)
        assert service is not None and service._conversation is not None
        return service

    a = await create("a")
    b = await create("b")
    trace = SimpleNamespace(a_entries=[], b_release=asyncio.Event())

    conv_a = a._conversation

    async def a_arun():
        # Mirror LocalConversation.arun: a stopped conversation is resumed.
        state = conv_a._state
        trace.a_entries.append(state.execution_status)
        with state:
            state.execution_status = ConversationExecutionStatus.RUNNING
        with state:
            state.execution_status = ConversationExecutionStatus.IDLE

    async def b_arun():
        await trace.b_release.wait()

    monkeypatch.setattr(conv_a, "arun", a_arun)
    monkeypatch.setattr(b._conversation, "arun", b_arun)

    tail_entered = threading.Event()
    tail_release = threading.Event()
    wrapper = a._callback_wrapper
    assert wrapper is not None
    original = wrapper.wait_for_pending

    def blocking_wait_for_pending(timeout=None):
        if not tail_release.is_set():
            tail_entered.set()
            assert tail_release.wait(timeout=10)
        original(timeout)

    monkeypatch.setattr(wrapper, "wait_for_pending", blocking_wait_for_pending)
    return a, b, trace, tail_entered, tail_release


async def _park_rearm_behind_b(owner, a, b, trace, tail_entered, tail_release):
    await a.run()
    assert await asyncio.to_thread(tail_entered.wait, 10)
    # A already returned its permit; B takes the only one.
    await b.run()
    assert owner._run_semaphore.locked()
    # Refused (A's task is still set) and parked for the re-arm.
    await a.send_message(
        Message(role="user", content=[TextContent(text="parked")]), run=True
    )
    assert a._rerun_requested is True
    tail_release.set()
    # A's tail ends and the re-arm blocks on the semaphore.
    await _wait_until(lambda: a._run_task is None and a._run_lock.locked())


@pytest.mark.asyncio
async def test_pause_while_rearm_waits_for_capacity_wins(tmp_path, monkeypatch):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", max_concurrent_runs=1
    ) as owner:
        a, b, trace, entered, release = await _setup(owner, tmp_path, monkeypatch)
        await _park_rearm_behind_b(owner, a, b, trace, entered, release)

        await a.pause()
        assert (await a.get_state()).execution_status == (
            ConversationExecutionStatus.PAUSED
        )

        trace.b_release.set()
        await b.wait_for_run_completion(5)
        await asyncio.sleep(0.3)
        await a.wait_for_run_completion(5)

        # Exactly one run of A (the original). A second entry that observed
        # PAUSED means the re-arm resumed a conversation the user had paused.
        assert trace.a_entries == [ConversationExecutionStatus.IDLE], trace.a_entries
        assert (await a.get_state()).execution_status == (
            ConversationExecutionStatus.PAUSED
        )


@pytest.mark.asyncio
async def test_delete_while_rearm_waits_does_not_resurrect_files(tmp_path, monkeypatch):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", max_concurrent_runs=1
    ) as owner:
        a, b, trace, entered, release = await _setup(owner, tmp_path, monkeypatch)
        conv_a = a._conversation
        original_arun = conv_a.arun

        async def a_arun_writing():
            await original_arun()
            # Any event a real step would persist (action, observation, ...).
            conv_a._on_event(PauseEvent())

        monkeypatch.setattr(conv_a, "arun", a_arun_writing)
        await _park_rearm_behind_b(owner, a, b, trace, entered, release)

        conversation_dir = a.conversation_dir
        assert await owner.delete_conversation(a.stored.id)
        assert not conversation_dir.exists()

        trace.b_release.set()
        await b.wait_for_run_completion(5)
        await asyncio.sleep(0.3)

        leftovers = (
            sorted(
                p.relative_to(conversation_dir).as_posix()
                for p in conversation_dir.rglob("*")
            )
            if conversation_dir.exists()
            else []
        )
        assert len(trace.a_entries) == 1, (trace.a_entries, leftovers)
        assert not conversation_dir.exists(), leftovers
        # The dropped re-arm returned the permit it acquired.
        assert a._run_session_slot is None
        semaphore = owner._run_semaphore
        assert semaphore is not None and not semaphore.locked()
