"""Test: thread-pool exhaustion must not deadlock the lifecycle lock.

``_get_or_load_event_service`` acquires ``lifecycle_lock`` and then calls
``asyncio.to_thread(_prepare_persisted_runtime)`` inside the lock.  If the
default thread pool is exhausted, the ``to_thread`` call queues indefinitely
while still holding the lock.  Every subsequent ``get_event_service`` call —
including the WebSocket event-stream path and the REST ``/events/search``
endpoint — blocks waiting for the lock, making the entire agent-server appear
wedged even though simple endpoints (``/ready``, ``/api/settings``) still
respond.

This test asserts the **correct** behavior:

* Loading an already-cached conversation (no ``to_thread`` needed) must
  succeed immediately even when the thread pool is full, because the
  lifecycle lock must not be held while waiting for a thread.

The test currently **fails** on unpatched code, demonstrating the bug.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


async def _create_persisted_conversation(
    conversations_dir: Path, workspace_dir: Path
) -> UUID:
    """Create a conversation on disk so it can be loaded later."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(request)
    return info.id


@pytest.mark.asyncio
async def test_thread_pool_exhaustion_does_not_block_cached_conversation(
    tmp_path,
):
    """Loading an already-cached conversation must not deadlock when the
    thread pool is exhausted.

    The lifecycle lock is held while ``asyncio.to_thread`` waits for a free
    thread.  If the pool is full, the lock is held indefinitely, blocking
    every subsequent ``get_event_service`` call — even for conversations that
    are already in the cache and need no thread work at all.

    This is the root cause of the "events don't load" production incident.
    """
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"

    # 1. Create two persisted conversations on disk.
    conv_a = await _create_persisted_conversation(
        conversations_dir, workspace_dir / "a"
    )
    conv_b = await _create_persisted_conversation(
        conversations_dir, workspace_dir / "b"
    )

    # 2. Replace the event-loop's default executor with a 1-worker pool.
    loop = asyncio.get_running_loop()
    tiny_pool = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(tiny_pool)

    service = ConversationService(conversations_dir=conversations_dir)
    await service.__aenter__()
    block_event: threading.Event | None = None
    block_task: asyncio.Task[None] | None = None
    try:
        # Pre-load both conversations into the cache.
        es_a = await service.get_event_service(conv_a)
        assert es_a is not None
        es_b = await service.get_event_service(conv_b)
        assert es_b is not None

        # 3. Saturate the 1-thread pool with a blocking call that never
        #    returns on its own.
        block_event = threading.Event()

        async def _block_pool() -> None:
            await loop.run_in_executor(None, block_event.wait)

        block_task = asyncio.create_task(_block_pool())
        await asyncio.sleep(0.3)

        # 4. Evict conv_a from the cache so the next load needs
        #    asyncio.to_thread(_prepare_persisted_runtime) — this call will
        #    hang waiting for a thread.
        if service._event_services is not None:
            service._event_services.pop(conv_a, None)

        # Kick off the stuck load in the background.  Give it time to
        # acquire the lifecycle lock and enter asyncio.to_thread before we
        # try the cached load below.
        stuck_task = asyncio.create_task(service.get_event_service(conv_a))
        await asyncio.sleep(0.3)

        # 5. Loading conv_b — which IS in the cache and needs no thread work —
        #    must succeed immediately.  The lifecycle lock must not be held
        #    while waiting for a thread.
        es_b2 = await asyncio.wait_for(service.get_event_service(conv_b), timeout=3.0)
        assert es_b2 is not None
        assert es_b2 is es_b

        # Clean up the stuck task.
        block_event.set()
        try:
            await asyncio.wait_for(stuck_task, timeout=10.0)
        except (TimeoutError, asyncio.CancelledError):
            stuck_task.cancel()
    finally:
        if block_event is not None:
            block_event.set()
        if block_task is not None:
            try:
                await asyncio.wait_for(block_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                block_task.cancel()
        await service.__aexit__(None, None, None)
        tiny_pool.shutdown(wait=False)
