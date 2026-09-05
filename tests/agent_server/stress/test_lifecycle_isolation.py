"""Stress test: one stuck close must not wedge unrelated conversations.

Bug class this catches:
    - A process-wide lifecycle lock held across ``EventService.close()``.
      If one close hangs, create, load, and delete operations for every other
      conversation queue behind it indefinitely (#4514, fixed by #4570).

The blocking subscriber delays a real EventService close at its normal pub/sub
teardown boundary. All operations under test still use the production
ConversationService and persistence paths with credential-free TestLLMs.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.event import Event
from tests.agent_server.stress.budgets import LIFECYCLE_ISOLATION
from tests.agent_server.stress.scripts import (
    SlowTestLLM,
    start_conversation_with_test_llm,
    text_message,
)


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


@dataclass(slots=True)
class _BlockingCloseSubscriber(Subscriber[Event]):
    close_entered: asyncio.Event = field(default_factory=asyncio.Event)
    release_close: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, event: Event) -> None:
        pass

    async def close(self) -> None:
        self.close_entered.set()
        await self.release_close.wait()


def _idle_test_llm() -> SlowTestLLM:
    llm = SlowTestLLM.from_messages([text_message("done")], latency_s=0.0)
    assert isinstance(llm, SlowTestLLM)
    return llm


async def _create_idle_conversation(
    conversation_service: ConversationService,
    *,
    workspace_dir: str,
    usage_id: str,
):
    return await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=_idle_test_llm(),
        workspace_dir=workspace_dir,
        usage_id=usage_id,
        initial_text=None,
    )


async def test_stuck_close_does_not_block_unrelated_lifecycle_operations(
    conversation_service: ConversationService,
    tmp_path,
):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    blocked = await _create_idle_conversation(
        conversation_service,
        workspace_dir=str(workspace),
        usage_id="lifecycle-blocked",
    )
    unrelated = await asyncio.gather(
        *[
            _create_idle_conversation(
                conversation_service,
                workspace_dir=str(workspace),
                usage_id=f"lifecycle-unrelated-{i}",
            )
            for i in range(LIFECYCLE_ISOLATION.n_unrelated_conversations)
        ]
    )

    blocked_service = await conversation_service.get_event_service(blocked.id)
    assert blocked_service is not None
    blocker = _BlockingCloseSubscriber()
    blocked_service._pub_sub.subscribe(blocker)

    blocked_delete = asyncio.create_task(
        conversation_service.delete_conversation(blocked.id)
    )
    try:
        await asyncio.wait_for(
            blocker.close_entered.wait(),
            timeout=LIFECYCLE_ISOLATION.unrelated_operations_timeout_s,
        )

        started_at = time.monotonic()
        load_task = asyncio.create_task(
            conversation_service.get_event_service(unrelated[0].id)
        )
        delete_tasks = [
            asyncio.create_task(conversation_service.delete_conversation(info.id))
            for info in unrelated[1:]
        ]
        create_task = asyncio.create_task(
            _create_idle_conversation(
                conversation_service,
                workspace_dir=str(workspace),
                usage_id="lifecycle-created-during-close",
            )
        )
        operations: list[asyncio.Task[Any]] = [
            load_task,
            *delete_tasks,
            create_task,
        ]
        _done, pending = await asyncio.wait(
            operations,
            timeout=LIFECYCLE_ISOLATION.unrelated_operations_timeout_s,
        )
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise AssertionError(
                "unrelated conversation create/load/delete operations blocked "
                "behind a stuck close; lifecycle work may be globally serialized"
            )
        elapsed = time.monotonic() - started_at

        assert load_task.result() is not None
        assert all(task.result() for task in delete_tasks)
        created = create_task.result()
        assert await conversation_service.get_event_service(created.id) is not None
        assert elapsed < LIFECYCLE_ISOLATION.unrelated_operations_timeout_s
        assert not blocked_delete.done(), (
            "blocked close unexpectedly completed before its subscriber was released"
        )
    finally:
        blocker.release_close.set()
        assert await asyncio.wait_for(
            blocked_delete,
            timeout=LIFECYCLE_ISOLATION.unrelated_operations_timeout_s,
        )
