"""Idle-conversation eviction in ``ConversationService`` (OSS-1495)."""

import asyncio
import time

import pytest
from pydantic import SecretStr

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import LLM, Agent, Event
from openhands.sdk.credential import ResolvedCredential
from openhands.sdk.secret import StaticSecret
from openhands.sdk.security.confirmation_policy import AlwaysConfirm, NeverConfirm
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.workspace import LocalWorkspace


def _make_request(workspace_dir) -> StartConversationRequest:
    workspace_dir.mkdir()
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )


class _DummySubscriber(Subscriber):
    """Stand-in for an external (e.g. websocket) subscriber."""

    async def __call__(self, event: Event) -> None:
        return None


class _FakeBinding:
    """Minimal VersionedCredentialBinding stub (only its identity is checked)."""

    async def load(self) -> ResolvedCredential:
        return ResolvedCredential(value="v", version="1")

    async def replace(self, expected_version: str, value: str) -> str:
        return value


@pytest.mark.asyncio
async def test_eviction_task_started_only_when_configured(tmp_path):
    conversations_dir = tmp_path / "conversations"

    async with ConversationService(conversations_dir=conversations_dir) as service:
        assert service._eviction_task is None

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        assert service._eviction_task is not None
        assert not service._eviction_task.done()

    # The task is cancelled and cleared on shutdown.
    assert service._eviction_task is None


@pytest.mark.asyncio
async def test_idle_conversation_is_evicted_and_rehydrated(tmp_path):
    """An idle conversation is evicted from memory but re-hydrates on access.

    This reproduces the reported leak: without eviction a finished
    conversation stays fully loaded in ``_event_services`` until it is
    explicitly deleted or the server restarts.
    """
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id

        assert service._event_services is not None
        event_service = service._event_services[conversation_id]
        assert event_service.is_open()

        # Simulate the conversation having gone idle well past the TTL.
        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)

        # The runtime is gone from memory, but the catalog record survives.
        assert conversation_id not in service._event_services
        assert conversation_id in service._conversation_records
        assert not event_service.is_open()

        # Next access transparently re-hydrates a fresh runtime from disk.
        rehydrated = await service.get_event_service(conversation_id)
        assert rehydrated is not None
        assert rehydrated is not event_service
        assert conversation_id in service._event_services


@pytest.mark.asyncio
async def test_recently_active_conversation_is_not_evicted(tmp_path):
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id

        # Freshly started: idle time is ~0, so it stays resident.
        await service._evict_idle_conversations(1800)

        assert service._event_services is not None
        assert conversation_id in service._event_services


@pytest.mark.asyncio
async def test_access_defers_eviction(tmp_path):
    """Accessing a live runtime refreshes its idle clock (touch)."""
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id
        assert service._event_services is not None
        event_service = service._event_services[conversation_id]

        # Age the conversation past the TTL, then access it.
        event_service._last_active_monotonic = time.monotonic() - 10_000
        assert await service.get_event_service(conversation_id) is event_service
        assert event_service.idle_seconds() < 1800

        # Because the access refreshed the clock, eviction leaves it in place.
        await service._evict_idle_conversations(1800)
        assert conversation_id in service._event_services


@pytest.mark.asyncio
async def test_running_conversation_is_not_evicted(tmp_path):
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id
        assert service._event_services is not None
        event_service = service._event_services[conversation_id]

        event_service._last_active_monotonic = time.monotonic() - 10_000
        # Simulate an in-flight agent run.
        run_task = asyncio.create_task(asyncio.sleep(3600))
        event_service._run_task = run_task
        try:
            await service._evict_idle_conversations(1800)
            assert conversation_id in service._event_services
            assert event_service.is_open()
        finally:
            run_task.cancel()
            event_service._run_task = None


@pytest.mark.asyncio
async def test_conversation_with_external_subscriber_is_not_evicted(tmp_path):
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id
        assert service._event_services is not None
        event_service = service._event_services[conversation_id]

        # A websocket-style external subscriber attaches after startup.
        assert not event_service.has_external_subscribers()
        subscriber_id = await event_service.subscribe_to_events(_DummySubscriber())
        assert event_service.has_external_subscribers()

        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)
        assert conversation_id in service._event_services
        assert event_service.is_open()

        # Once the subscriber disconnects, the conversation becomes evictable.
        await event_service.unsubscribe_from_events(subscriber_id)
        assert not event_service.has_external_subscribers()
        await service._evict_idle_conversations(1800)
        assert conversation_id not in service._event_services
        assert conversation_id in service._conversation_records


@pytest.mark.asyncio
async def test_eviction_preserves_credential_bindings(tmp_path):
    """Runtime-only credential bindings survive eviction so rehydration keeps them."""
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id
        assert service._event_services is not None
        event_service = service._event_services[conversation_id]

        # A binding activated at runtime lives only on the EventService and is
        # cleared by close(); it cannot be re-derived from disk.
        binding = _FakeBinding()
        event_service.credential_bindings = {"MY_SECRET": binding}

        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)

        assert conversation_id not in service._event_services
        # The binding is handed back to the catalog for the next hydration.
        assert (
            service._credential_bindings.get(conversation_id, {}).get("MY_SECRET")
            is binding
        )


@pytest.mark.asyncio
async def test_eviction_preserves_reassigned_stored_metadata(tmp_path):
    """A stored replaced at runtime (e.g. switch_acp_model) survives rehydration."""
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace")

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        conversation_id = info.id
        assert service._event_services is not None
        event_service = service._event_services[conversation_id]

        # switch_acp_model / secret updates *replace* event_service.stored and
        # persist it; the stale catalog object must not be used on rehydration.
        event_service.stored = event_service.stored.model_copy(
            update={"title": "new-title"}
        )
        await event_service.save_meta()

        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)
        assert conversation_id not in service._event_services

        rehydrated = await service.get_event_service(conversation_id)
        assert rehydrated is not None
        assert rehydrated.stored.title == "new-title"


@pytest.mark.asyncio
async def test_eviction_keeps_runtime_policy_analyzer_and_secrets(tmp_path):
    """What a client set after creation is still in force once the idle
    conversation is evicted and loaded again (#4810)."""
    conversations_dir = tmp_path / "conversations"
    request = _make_request(tmp_path / "workspace").model_copy(
        update={"secrets": {"TOKEN": StaticSecret(value=SecretStr("start-value"))}}
    )

    async with ConversationService(
        conversations_dir=conversations_dir,
        conversation_idle_ttl_seconds=1800,
        cipher=Cipher("eviction-key"),
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services[info.id]
        await event_service.set_confirmation_policy(AlwaysConfirm())
        await event_service.set_security_analyzer(LLMSecurityAnalyzer())
        await event_service.update_secrets({"TOKEN": "runtime-value"})

        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)
        assert info.id not in service._event_services

        rehydrated = await service.get_event_service(info.id)
        assert rehydrated is not None
        state = await rehydrated.get_state()
        assert isinstance(state.confirmation_policy, AlwaysConfirm)
        assert isinstance(state.security_analyzer, LLMSecurityAnalyzer)
        token = state.secret_registry.secret_sources["TOKEN"]
        assert token.get_value() == "runtime-value"


@pytest.mark.asyncio
async def test_conversation_whose_secrets_cannot_be_reloaded_is_not_evicted(tmp_path):
    """Without a cipher base_state.json has no secret values. The loaded
    conversation is their only copy, so it stays loaded."""
    request = _make_request(tmp_path / "workspace").model_copy(
        update={"secrets": {"TOKEN": StaticSecret(value=SecretStr("start-value"))}}
    )

    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        conversation_idle_ttl_seconds=1800,
    ) as service:
        info, _ = await service.start_conversation(request)
        assert service._event_services is not None
        event_service = service._event_services[info.id]

        event_service._last_active_monotonic = time.monotonic() - 10_000
        await service._evict_idle_conversations(1800)

        assert service._event_services[info.id] is event_service
        token = (await event_service.get_state()).secret_registry.secret_sources
        assert token["TOKEN"].get_value() == "start-value"
