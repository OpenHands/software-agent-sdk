import asyncio
import threading
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import WaitForChildrenRequest
from openhands.sdk import Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus as Status


pytestmark = pytest.mark.timeout(15)


@pytest.mark.parametrize("complete_before_wait", [False, True])
async def test_completion_and_subscription_race(family, complete_before_wait):
    svc, parent, children, entered, release, waiting, _, _ = family
    child = children[0]
    await child.run()
    await entered[child.stored.id].wait()
    if complete_before_wait:
        release[child.stored.id].set()
        await child.wait_for_run_completion()
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(conversation_ids=[child.stored.id]),
        )
    )
    if not complete_before_wait:
        await waiting[child.stored.id].wait()
        release[child.stored.id].set()
    result = await task
    assert not result.timed_out and not result.interrupted
    assert result.children[0].execution_status == Status.FINISHED
    assert result.children[0].settled
    assert await svc.get_conversation(child.stored.id) is not None
    assert not child.has_external_subscribers()
    assert parent.is_idle_evictable()


async def test_multiple_children_and_parent_progress_concurrently(family):
    svc, parent, children, entered, release, waiting, _, _ = family
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(conversation_ids=[c.stored.id for c in children]),
        )
    )
    for child in children:
        await waiting[child.stored.id].wait()
    assert not parent.is_idle_evictable()
    assert all(not c.is_idle_evictable() for c in children)
    # Starting the parent and all children must remain possible while waiting.
    for conversation in [parent, *children]:
        await conversation.run()
        await entered[conversation.stored.id].wait()
        release[conversation.stored.id].set()
    result = await task
    assert all(
        c.settled and c.execution_status == Status.FINISHED for c in result.children
    )
    assert not result.timed_out
    await parent.wait_for_run_completion()
    assert parent.is_idle_evictable()


@pytest.mark.parametrize("on_timeout", ["retain", "stop", "delete"])
async def test_timeout_and_cleanup(family, on_timeout):
    svc, parent, children, entered, release, _, _, _ = family
    child = children[0]
    await child.run()
    await entered[child.stored.id].wait()
    result = await svc.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id],
            timeout_seconds=0,
            on_timeout=on_timeout,
        ),
    )
    assert result.timed_out and not result.interrupted
    assert result.children[0].execution_status == Status.RUNNING
    assert not result.children[0].settled
    info = await svc.get_conversation(child.stored.id)
    if on_timeout == "delete":
        assert info is None and result.children[0].deleted
        assert not child.conversation_dir.exists()
    else:
        assert info is not None and not result.children[0].deleted
        if on_timeout == "stop":
            assert info.execution_status == Status.PAUSED
        else:
            assert info.execution_status == Status.RUNNING
            release[child.stored.id].set()
            assert await child.wait_for_run_completion() == Status.FINISHED
    assert parent.is_idle_evictable()


@pytest.mark.parametrize("delete", [False, True])
async def test_finish_cleanup(family, delete):
    svc, parent, children, _, release, _, _, _ = family
    child = children[0]
    release[child.stored.id].set()
    await child.run()
    await child.wait_for_run_completion()
    result = await svc.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id],
            delete_on_finish=delete,
        ),
    )
    assert result.children[0].deleted == delete
    assert (await svc.get_conversation(child.stored.id) is None) == delete
    assert child.conversation_dir.exists() != delete


@pytest.mark.parametrize("status", [Status.ERROR, Status.STUCK, Status.PAUSED])
async def test_child_failure_or_termination(family, status):
    svc, parent, children, entered, release, waiting, outcomes, _ = family
    child = children[0]
    outcomes[child.stored.id] = status
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(conversation_ids=[child.stored.id]),
        )
    )
    await waiting[child.stored.id].wait()
    await child.run()
    await entered[child.stored.id].wait()
    release[child.stored.id].set()
    result = await task
    assert result.children[0].execution_status == status
    assert result.children[0].settled and not result.timed_out


@pytest.mark.parametrize("action", ["cancel", "interrupt", "close"])
async def test_parent_cancellation_releases_waiters_and_keeps_child(family, action):
    svc, parent, children, entered, release, waiting, _, _ = family
    child = children[0]
    await child.run()
    await entered[child.stored.id].wait()
    before = asyncio.all_tasks()
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(
                conversation_ids=[child.stored.id],
                on_timeout="delete",
            ),
        )
    )
    await waiting[child.stored.id].wait()
    if action == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if action == "interrupt":
            await parent.interrupt()
        else:
            await svc.delete_conversation(parent.stored.id)
        result = await task
        assert result.interrupted and not result.timed_out
    assert not (asyncio.all_tasks() - before)
    assert await svc.get_conversation(child.stored.id) is not None
    release[child.stored.id].set()
    assert await child.wait_for_run_completion() == Status.FINISHED
    assert child.is_idle_evictable()


async def test_invalid_children_are_rejected_before_cleanup(family):
    svc, parent, children, _, _, _, _, _ = family
    for ids in [[uuid4()], [parent.stored.id], [children[0].stored.id, uuid4()]]:
        with pytest.raises(ValueError, match="Child conversation not found"):
            await svc.wait_for_children(
                parent.stored.id,
                WaitForChildrenRequest(conversation_ids=ids, on_timeout="delete"),
            )
    with pytest.raises(ValueError, match="Parent conversation not found"):
        await svc.wait_for_children(
            uuid4(), WaitForChildrenRequest(conversation_ids=[children[0].stored.id])
        )
    with pytest.raises(ValueError, match="Child conversation not found"):
        await svc.wait_for_children(
            children[1].stored.id,
            WaitForChildrenRequest(conversation_ids=[children[0].stored.id]),
        )
    assert await svc.get_conversation(children[0].stored.id) is not None


async def test_provisional_finished_does_not_complete_wait(family):
    svc, parent, children, entered, release, waiting, _, provisional = family
    child = children[0]
    provisional[child.stored.id] = asyncio.Event()
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(conversation_ids=[child.stored.id]),
        )
    )
    await waiting[child.stored.id].wait()
    await child.run()
    await entered[child.stored.id].wait()
    entered[child.stored.id].clear()
    release[child.stored.id].set()
    await entered[child.stored.id].wait()
    # A locked snapshot proves that the step has reached FINISHED while the
    # run remains suspended, just as it can during a stop hook.
    assert await child._get_execution_status() == Status.FINISHED
    assert not task.done()
    provisional[child.stored.id].set()
    assert (await task).children[0].settled


async def test_rest_contract_without_websocket(family):
    svc, parent, children, _, release, _, _, _ = family
    child = children[0]
    release[child.stored.id].set()
    await child.run()
    await child.wait_for_run_completion()
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: svc
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        url = f"/api/conversations/{parent.stored.id}/children/wait"
        response = await client.post(
            url, json={"conversation_ids": [str(child.stored.id)]}
        )
        assert response.status_code == 200
        assert response.json()["children"][0]["execution_status"] == "finished"
        response = await client.post(url, json={"conversation_ids": [str(uuid4())]})
        assert response.status_code == 404
        for body in [
            {"conversation_ids": []},
            {"conversation_ids": [str(child.stored.id)] * 2},
            {"conversation_ids": [str(child.stored.id)], "timeout_seconds": 301},
        ]:
            assert (await client.post(url, json=body)).status_code == 422


async def test_timeout_does_not_acquire_held_child_state_lock(family):
    svc, parent, children, _, _, _, _, _ = family
    child = children[0]
    acquired = asyncio.Event()
    release_lock = threading.Event()
    loop = asyncio.get_running_loop()

    def hold_lock():
        with child.get_conversation()._state:
            loop.call_soon_threadsafe(acquired.set)
            assert release_lock.wait(5)

    holder = asyncio.create_task(asyncio.to_thread(hold_lock))
    await acquired.wait()
    try:
        result = await svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(
                conversation_ids=[child.stored.id], timeout_seconds=0
            ),
        )
        assert result.timed_out
        assert result.children[0].execution_status == Status.IDLE
    finally:
        release_lock.set()
        await holder


async def test_finish_cleanup_preserves_restarted_child(family, monkeypatch):
    svc, parent, children, entered, release, _, _, _ = family
    child = children[0]
    release[child.stored.id].set()
    await child.run()
    await child.wait_for_run_completion()
    original_delete = svc.delete_conversation

    async def restart_before_delete(conversation_id, **kwargs):
        release[child.stored.id].clear()
        entered[child.stored.id].clear()
        await child.send_message(
            Message(role="user", content=[TextContent(text="Next task")]), run=False
        )
        await child.run()
        await entered[child.stored.id].wait()
        return await original_delete(conversation_id, **kwargs)

    monkeypatch.setattr(svc, "delete_conversation", restart_before_delete)
    result = await svc.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id], delete_on_finish=True
        ),
    )
    assert result.children[0].settled and not result.children[0].deleted
    assert child.is_open()
    release[child.stored.id].set()
    assert await child.wait_for_run_completion() == Status.FINISHED


async def test_cancellation_drains_started_delete(family, monkeypatch):
    svc, parent, children, _, _, _, _, _ = family
    child = children[0]
    started = asyncio.Event()
    finish = asyncio.Event()
    original_delete = svc.delete_conversation

    async def controlled_delete(conversation_id, **kwargs):
        started.set()
        await finish.wait()
        return await original_delete(conversation_id, **kwargs)

    monkeypatch.setattr(svc, "delete_conversation", controlled_delete)
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(
                conversation_ids=[child.stored.id],
                timeout_seconds=0,
                on_timeout="delete",
            ),
        )
    )
    await started.wait()
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await svc.get_conversation(child.stored.id) is None
    assert parent.is_idle_evictable()


async def test_mixed_timeout_cleans_only_settled_children(family):
    svc, parent, children, _, release, _, _, _ = family
    done, pending = children[:2]
    release[done.stored.id].set()
    await done.run()
    await done.wait_for_run_completion()
    result = await svc.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[done.stored.id, pending.stored.id],
            timeout_seconds=0,
            delete_on_finish=True,
        ),
    )
    assert result.timed_out
    assert [c.settled for c in result.children] == [True, False]
    assert [c.deleted for c in result.children] == [True, False]
    assert await svc.get_conversation(done.stored.id) is None
    assert await svc.get_conversation(pending.stored.id) is not None


@pytest.mark.parametrize("policy", ["stop", "delete"])
async def test_timeout_cleanup_preserves_restarted_child(family, monkeypatch, policy):
    svc, parent, children, entered, release, _, _, _ = family
    child = children[0]
    original_delete = svc.delete_conversation
    original_interrupt = child.interrupt

    async def start_successor():
        await child.run()
        await entered[child.stored.id].wait()

    async def delete_after_restart(conversation_id, **kwargs):
        await start_successor()
        return await original_delete(conversation_id, **kwargs)

    async def interrupt_after_restart(**kwargs):
        await start_successor()
        return await original_interrupt(**kwargs)

    if policy == "delete":
        monkeypatch.setattr(svc, "delete_conversation", delete_after_restart)
    else:
        monkeypatch.setattr(child, "interrupt", interrupt_after_restart)
    result = await svc.wait_for_children(
        parent.stored.id,
        WaitForChildrenRequest(
            conversation_ids=[child.stored.id], timeout_seconds=0, on_timeout=policy
        ),
    )
    assert result.timed_out and not result.children[0].deleted
    info = await svc.get_conversation(child.stored.id)
    assert info is not None and info.execution_status == Status.RUNNING
    release[child.stored.id].set()
    await child.wait_for_run_completion()


async def test_child_deleted_while_waiting(family):
    svc, parent, children, _, _, waiting, _, _ = family
    child = children[0]
    task = asyncio.create_task(
        svc.wait_for_children(
            parent.stored.id,
            WaitForChildrenRequest(conversation_ids=[child.stored.id]),
        )
    )
    await waiting[child.stored.id].wait()
    assert await svc.delete_conversation(child.stored.id)
    result = await task
    assert result.children[0].settled
    assert result.children[0].execution_status == Status.DELETING
    assert not result.timed_out
