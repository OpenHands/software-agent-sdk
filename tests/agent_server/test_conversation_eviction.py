"""Tests for conversation idle eviction functionality.

These tests verify that the ConversationService correctly evicts idle
conversations from memory based on configured timeouts and limits.
"""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.utils import utc_now
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest.mark.asyncio
async def test_eviction_task_not_started_when_disabled(tmp_path):
    """Eviction task should not be started when both policies are disabled."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=None,
        max_loaded_conversations=None,
    ) as svc:
        assert svc._eviction_task is None


@pytest.mark.asyncio
async def test_eviction_task_started_with_idle_timeout(tmp_path):
    """Eviction task should be started when idle_timeout_seconds is set."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=300,
        max_loaded_conversations=None,
    ) as svc:
        assert svc._eviction_task is not None
        assert not svc._eviction_task.done()

    # Task should be cleaned up after __aexit__
    assert svc._eviction_task is None


@pytest.mark.asyncio
async def test_eviction_task_started_with_max_loaded(tmp_path):
    """Eviction task should be started when max_loaded_conversations is set."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=None,
        max_loaded_conversations=10,
    ) as svc:
        assert svc._eviction_task is not None
        assert not svc._eviction_task.done()


@pytest.mark.asyncio
async def test_idle_timeout_evicts_finished_conversation(tmp_path):
    """A finished conversation should be evicted after idle timeout."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    # Use a very short timeout and eviction interval for testing
    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=60,
        max_loaded_conversations=None,
    ) as svc:
        info, _ = await svc.start_conversation(request)
        conversation_id = info.id

        assert svc._event_services is not None
        event_service = svc._event_services[conversation_id]

        # Mark conversation as finished
        state = await event_service.get_state()
        state.execution_status = ConversationExecutionStatus.FINISHED

        # Set updated_at to 2 minutes ago (beyond the 60s timeout)
        event_service.stored.updated_at = utc_now() - timedelta(minutes=2)

        # Run eviction cycle manually
        await svc._run_eviction_cycle()

        # Conversation should be evicted
        assert conversation_id not in svc._event_services


@pytest.mark.asyncio
async def test_idle_timeout_does_not_evict_running_conversation(tmp_path):
    """A running conversation should not be evicted even if idle."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=60,
        max_loaded_conversations=None,
    ) as svc:
        info, _ = await svc.start_conversation(request)
        conversation_id = info.id

        assert svc._event_services is not None
        event_service = svc._event_services[conversation_id]

        # Keep conversation running (default IDLE status is not terminal)
        state = await event_service.get_state()
        assert not state.execution_status.is_terminal()

        # Set updated_at to 2 minutes ago
        event_service.stored.updated_at = utc_now() - timedelta(minutes=2)

        # Run eviction cycle manually
        await svc._run_eviction_cycle()

        # Conversation should NOT be evicted (still running/idle)
        assert conversation_id in svc._event_services


@pytest.mark.asyncio
async def test_max_loaded_evicts_oldest_finished_first(tmp_path):
    """When over max_loaded, oldest finished conversations are evicted first."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=None,
        max_loaded_conversations=2,
    ) as svc:
        # Create 3 conversations
        info1, _ = await svc.start_conversation(request)
        info2, _ = await svc.start_conversation(request)
        info3, _ = await svc.start_conversation(request)

        assert svc._event_services is not None
        es1 = svc._event_services[info1.id]
        es2 = svc._event_services[info2.id]
        es3 = svc._event_services[info3.id]

        # Mark all as finished
        for es in [es1, es2, es3]:
            state = await es.get_state()
            state.execution_status = ConversationExecutionStatus.FINISHED

        # Set different idle times (oldest = longest idle)
        es1.stored.updated_at = utc_now() - timedelta(minutes=30)  # Oldest
        es2.stored.updated_at = utc_now() - timedelta(minutes=20)  # Middle
        es3.stored.updated_at = utc_now() - timedelta(minutes=10)  # Newest

        # Run eviction cycle - should evict 1 to get down to max_loaded=2
        await svc._run_eviction_cycle()

        # es1 (oldest) should be evicted, es2 and es3 should remain
        assert info1.id not in svc._event_services, (
            "Oldest conversation should be evicted"
        )
        assert info2.id in svc._event_services
        assert info3.id in svc._event_services


@pytest.mark.asyncio
async def test_eviction_preserves_non_terminal_conversations(tmp_path):
    """Non-terminal conversations should be preserved even when over limit."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=None,
        max_loaded_conversations=1,
    ) as svc:
        # Create 2 conversations
        info1, _ = await svc.start_conversation(request)
        info2, _ = await svc.start_conversation(request)

        assert svc._event_services is not None
        es2 = svc._event_services[info2.id]

        # info1 is running (not terminal), es2 is finished
        state2 = await es2.get_state()
        state2.execution_status = ConversationExecutionStatus.FINISHED
        es2.stored.updated_at = utc_now() - timedelta(minutes=10)

        # Run eviction cycle
        await svc._run_eviction_cycle()

        # es1 (running) should be preserved, es2 (finished) should be evicted
        assert info1.id in svc._event_services, (
            "Running conversation should be preserved"
        )
        assert info2.id not in svc._event_services, (
            "Finished conversation should be evicted"
        )


@pytest.mark.asyncio
async def test_eviction_loop_runs_periodically(tmp_path):
    """The eviction loop should run periodically."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    run_count = 0
    original_run_cycle = ConversationService._run_eviction_cycle

    async def counting_run_cycle(self):
        nonlocal run_count
        run_count += 1
        await original_run_cycle(self)

    with patch.object(ConversationService, "_run_eviction_cycle", counting_run_cycle):
        # Patch the eviction interval to be very short
        with patch(
            "openhands.agent_server.conversation_service.ConversationService._eviction_loop",
            wraps=None,
        ):
            async with ConversationService(
                conversations_dir=conversations_dir,
                idle_timeout_seconds=60,
            ) as svc:
                # Replace the task with one that uses a shorter interval
                if svc._eviction_task:
                    svc._eviction_task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await svc._eviction_task

                # Run a few cycles manually
                await svc._run_eviction_cycle()
                await svc._run_eviction_cycle()

                assert run_count >= 2


@pytest.mark.asyncio
async def test_evicted_conversation_can_be_rehydrated(tmp_path):
    """An evicted conversation should be able to be rehydrated from disk."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    # Start conversation, evict it, then restart service to rehydrate
    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=60,
    ) as svc:
        info, _ = await svc.start_conversation(request)
        conversation_id = info.id

        assert svc._event_services is not None
        event_service = svc._event_services[conversation_id]

        # Mark conversation as finished and make it old
        state = await event_service.get_state()
        state.execution_status = ConversationExecutionStatus.FINISHED
        event_service.stored.updated_at = utc_now() - timedelta(minutes=2)

        # Evict the conversation
        await svc._run_eviction_cycle()
        assert conversation_id not in svc._event_services

    # Restart service - conversation should be rehydrated from disk
    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=60,
    ) as svc:
        assert svc._event_services is not None
        # Conversation should be loaded from disk
        assert conversation_id in svc._event_services


@pytest.mark.asyncio
async def test_combined_idle_timeout_and_max_loaded(tmp_path):
    """Both idle_timeout and max_loaded should work together."""
    conversations_dir = tmp_path / "conversations"
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(
        conversations_dir=conversations_dir,
        idle_timeout_seconds=300,  # 5 minutes
        max_loaded_conversations=3,
    ) as svc:
        # Create 4 conversations
        infos = []
        for _ in range(4):
            info, _ = await svc.start_conversation(request)
            infos.append(info)

        assert svc._event_services is not None

        # Mark all as finished with different idle times
        for i, info in enumerate(infos):
            es = svc._event_services[info.id]
            state = await es.get_state()
            state.execution_status = ConversationExecutionStatus.FINISHED
            # First two are past idle timeout, last two are not
            if i < 2:
                es.stored.updated_at = utc_now() - timedelta(minutes=10)
            else:
                es.stored.updated_at = utc_now() - timedelta(minutes=1)

        # Run eviction cycle
        await svc._run_eviction_cycle()

        # First two should be evicted due to idle timeout
        # Third and fourth should remain (within timeout and within max)
        assert infos[0].id not in svc._event_services
        assert infos[1].id not in svc._event_services
        assert infos[2].id in svc._event_services
        assert infos[3].id in svc._event_services
