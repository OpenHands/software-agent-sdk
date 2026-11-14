"""A stop during a re-arm's wait for run capacity must win."""

import asyncio
import threading

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import PauseEvent
from openhands.sdk.workspace import LocalWorkspace


async def _rearm_waiting_for_capacity(owner, tmp_path, monkeypatch):
    """A finishes, B takes the only permit, A's parked input re-arms and waits."""
    services = []
    for name in ("a", "b"):
        info, _ = await owner.start_conversation(
            StartConversationRequest(
                agent=Agent(llm=LLM(model="gpt-4o", usage_id=name), tools=[]),
                workspace=LocalWorkspace(working_dir=str(tmp_path)),
                autotitle=False,
            )
        )
        services.append(await owner.get_event_service(info.id))
    a, b = services
    runs_of_a: list[ConversationExecutionStatus] = []
    release_b = asyncio.Event()

    conversation = a.get_conversation()

    async def a_arun():
        runs_of_a.append(conversation.state.execution_status)
        with conversation.state as state:
            state.execution_status = ConversationExecutionStatus.IDLE
        conversation._on_event(PauseEvent())

    monkeypatch.setattr(conversation, "arun", a_arun)
    monkeypatch.setattr(b._conversation, "arun", release_b.wait)

    in_tail, leave_tail = threading.Event(), threading.Event()
    wait_for_pending = a._callback_wrapper.wait_for_pending

    def held_wait_for_pending(timeout=None):
        if not leave_tail.is_set():
            in_tail.set()
            leave_tail.wait(10)
        wait_for_pending(timeout)

    monkeypatch.setattr(a._callback_wrapper, "wait_for_pending", held_wait_for_pending)

    await a.run()
    assert await asyncio.to_thread(in_tail.wait, 10)
    await b.run()
    await a.send_message(Message(role="user", content=[TextContent(text="x")]), True)
    assert a._rerun_requested
    leave_tail.set()
    async with asyncio.timeout(5):
        while not (a._run_task is None and a._run_lock.locked()):
            await asyncio.sleep(0.01)
    return a, b, runs_of_a, release_b


@pytest.mark.asyncio
async def test_pause_while_rearm_waits_for_capacity_wins(tmp_path, monkeypatch):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", max_concurrent_runs=1
    ) as owner:
        a, b, runs_of_a, release_b = await _rearm_waiting_for_capacity(
            owner, tmp_path, monkeypatch
        )
        await a.pause()
        release_b.set()
        await b.wait_for_run_completion(5)
        await asyncio.sleep(0.3)

        assert runs_of_a == [ConversationExecutionStatus.IDLE]
        state = await a.get_state()
        assert state.execution_status == ConversationExecutionStatus.PAUSED


@pytest.mark.asyncio
async def test_delete_while_rearm_waits_does_not_resurrect_files(tmp_path, monkeypatch):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", max_concurrent_runs=1
    ) as owner:
        a, b, runs_of_a, release_b = await _rearm_waiting_for_capacity(
            owner, tmp_path, monkeypatch
        )
        assert await owner.delete_conversation(a.stored.id)
        release_b.set()
        await b.wait_for_run_completion(5)
        await asyncio.sleep(0.3)

        assert len(runs_of_a) == 1
        assert not a.conversation_dir.exists()
        assert a._run_session_slot is None
        assert owner._run_semaphore and not owner._run_semaphore.locked()
