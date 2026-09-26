"""Tests for idle container suspension in DockerConversationRegistry."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus


def _registry(tmp_path, monkeypatch) -> DockerConversationRegistry:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
            conversation_idle_ttl_seconds=120.0,
        )
    )


def _container(conversation_id: UUID) -> ConversationContainer:
    return ConversationContainer(
        host=f"http://127.0.0.1/{conversation_id}",
        api_key="inner-key",
        container_id=f"container-{conversation_id}",
    )


# ------------------------------------------------------------------
# _is_suspendable tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspendable_when_terminal_status(tmp_path, monkeypatch):
    """Container is suspendable when inner server reports suspendable=True."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.is_error = False
    mock_response.json.return_value = {"suspendable": True}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await reg._is_suspendable(cid, cont)
    assert result is True


@pytest.mark.asyncio
async def test_not_suspendable_when_inner_reports_false(tmp_path, monkeypatch):
    """Container is NOT suspendable when inner server reports suspendable=False."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.is_error = False
    mock_response.json.return_value = {"suspendable": False}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await reg._is_suspendable(cid, cont)
    assert result is False


@pytest.mark.asyncio
async def test_not_suspendable_when_inner_unreachable(tmp_path, monkeypatch):
    """If the inner container is unreachable, don't suspend it."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await reg._is_suspendable(cid, cont)
    assert result is False


@pytest.mark.asyncio
async def test_not_suspendable_when_inner_404(tmp_path, monkeypatch):
    """If inner server returns 404, don't suspend it."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.is_error = True

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await reg._is_suspendable(cid, cont)
    assert result is False


# ------------------------------------------------------------------
# _suspend_idle_containers tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_stops_terminal_container(tmp_path, monkeypatch):
    """A finished container should be stopped and removed from _containers."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)
    reg._containers[cid] = cont

    # Mock _is_suspendable → True
    reg._is_suspendable = AsyncMock(return_value=True)

    # Mock stop() to just remove from _containers
    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        stopped_ids.append(conversation_id)
        async with reg._lock:
            reg._containers.pop(conversation_id, None)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    await reg._suspend_idle_containers()

    assert cid in stopped_ids
    assert cid not in reg._containers


@pytest.mark.asyncio
async def test_suspend_skips_active_container(tmp_path, monkeypatch):
    """A running conversation should not be suspended."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)
    reg._containers[cid] = cont

    # Mock _is_suspendable → False (running)
    reg._is_suspendable = AsyncMock(return_value=False)

    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        stopped_ids.append(conversation_id)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    await reg._suspend_idle_containers()

    assert cid not in stopped_ids
    assert cid in reg._containers


@pytest.mark.asyncio
async def test_suspend_skips_deleting_container(tmp_path, monkeypatch):
    """A container being deleted should not be suspended."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)
    reg._containers[cid] = cont
    reg._deleting.add(cid)

    reg._is_suspendable = AsyncMock(return_value=True)

    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        stopped_ids.append(conversation_id)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    await reg._suspend_idle_containers()

    assert cid not in stopped_ids
    # _is_suspendable should not even be called for deleting containers
    reg._is_suspendable.assert_not_called()


@pytest.mark.asyncio
async def test_suspend_handles_stop_failure_gracefully(tmp_path, monkeypatch):
    """If stopping one container fails, others should still be processed."""
    reg = _registry(tmp_path, monkeypatch)
    cid1 = uuid4()
    cid2 = uuid4()
    reg._containers[cid1] = _container(cid1)
    reg._containers[cid2] = _container(cid2)

    reg._is_suspendable = AsyncMock(return_value=True)

    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        if conversation_id == cid1:
            raise RuntimeError("Docker daemon error")
        stopped_ids.append(conversation_id)
        async with reg._lock:
            reg._containers.pop(conversation_id, None)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    # Should not raise, even though stopping cid1 fails
    await reg._suspend_idle_containers()

    assert cid2 in stopped_ids
    assert cid1 in reg._containers  # still there because stop failed


# ------------------------------------------------------------------
# Resume after suspend (integration-like)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_after_suspend_via_get_or_create(tmp_path, monkeypatch):
    """After a container is suspended, get_or_create recreates it."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    original = _container(cid)
    reg._containers[cid] = original

    # Suspend: remove from _containers (simulating stop)
    async with reg._lock:
        reg._containers.pop(cid, None)

    # Now get_or_create should build a new container
    resumed = _container(cid)
    resumed.container_id = "new-container-id"
    reg._build_container = lambda conversation_id: resumed

    result = await reg.get_or_create(cid)
    assert result is resumed
    assert result.container_id == "new-container-id"
    assert reg.get(cid) is resumed


# ------------------------------------------------------------------
# start() and shutdown() lifecycle
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_creates_eviction_task_when_ttl_set(tmp_path, monkeypatch):
    """start() should launch the eviction loop when TTL is configured."""
    reg = _registry(tmp_path, monkeypatch)
    monkeypatch.setattr(reg, "cleanup_stale_containers", lambda: None)

    await reg.start()

    assert reg._eviction_task is not None
    assert not reg._eviction_task.done()

    # Cleanup
    reg._eviction_task.cancel()
    try:
        await reg._eviction_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_start_no_eviction_task_when_ttl_none(tmp_path, monkeypatch):
    """start() should NOT launch eviction loop when TTL is None."""
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    reg = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
            conversation_idle_ttl_seconds=None,
        )
    )
    monkeypatch.setattr(reg, "cleanup_stale_containers", lambda: None)

    await reg.start()

    assert reg._eviction_task is None


@pytest.mark.asyncio
async def test_shutdown_cancels_eviction_task(tmp_path, monkeypatch):
    """shutdown() should cancel the eviction loop task."""
    reg = _registry(tmp_path, monkeypatch)
    monkeypatch.setattr(reg, "cleanup_stale_containers", lambda: None)

    await reg.start()
    assert reg._eviction_task is not None

    await reg.shutdown()
    assert reg._eviction_task is None


# ------------------------------------------------------------------
# Lease tracking and atomic check-then-stop tests
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_skips_when_active_lease_held(tmp_path, monkeypatch):
    """A container with an active lease should NOT be suspended."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)
    reg._containers[cid] = cont

    # Acquire lease
    release = reg.acquire_lease(cid)
    assert reg.has_active_leases(cid)

    reg._is_suspendable = AsyncMock(return_value=True)
    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        stopped_ids.append(conversation_id)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    await reg._suspend_idle_containers()

    assert cid not in stopped_ids
    assert cid in reg._containers
    reg._is_suspendable.assert_not_called()

    # Once released, it becomes suspendable
    release()
    assert not reg.has_active_leases(cid)
    await reg._suspend_idle_containers()
    assert cid in stopped_ids


@pytest.mark.asyncio
async def test_suspend_aborts_when_re_engaged_during_check(tmp_path, monkeypatch):
    """If a request arrives while _is_suspendable is in flight, abort suspension."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = _container(cid)
    reg._containers[cid] = cont

    # Simulate request arriving during the probe
    async def mock_is_suspendable(conversation_id, container):
        # A request arrives and acquires a lease during the network probe
        reg.acquire_lease(conversation_id)
        return True

    reg._is_suspendable = mock_is_suspendable
    stopped_ids = []

    async def mock_stop(conversation_id, *args, **kwargs):
        stopped_ids.append(conversation_id)
        return True

    monkeypatch.setattr(reg, "_stop", mock_stop)

    await reg._suspend_idle_containers()

    # The container was re-engaged during probe, so it must not be stopped!
    assert cid not in stopped_ids
    assert cid in reg._containers


@pytest.mark.asyncio
async def test_get_or_create_waits_for_stopping_container(tmp_path, monkeypatch):
    """get_or_create should wait if container is stopping, then build fresh."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    original = _container(cid)
    reg._containers[cid] = original

    # When stop begins, the container is popped and marked stopping
    stop_event = asyncio.Event()
    reg._stopping[cid] = stop_event
    reg._containers.pop(cid, None)

    resumed = _container(cid)
    resumed.container_id = "rebuilt-after-stop"
    reg._build_container = lambda conversation_id: resumed

    task = asyncio.create_task(reg.get_or_create(cid))
    await asyncio.sleep(0.01)
    # task should be waiting on stop_event
    assert not task.done()

    # Simulate stop completion
    reg._stopping.pop(cid, None)
    stop_event.set()

    result = await task
    assert result is resumed
    assert result.container_id == "rebuilt-after-stop"


@pytest.mark.asyncio
async def test_is_suspendable_against_real_inner_app(tmp_path, monkeypatch):
    """Verify _is_suspendable against real FastAPI inner routes via ASGITransport."""
    reg = _registry(tmp_path, monkeypatch)
    cid = uuid4()
    cont = ConversationContainer(
        host="http://127.0.0.1",
        api_key="inner-key",
        container_id=f"container-{cid}",
    )

    inner_app = FastAPI()
    inner_app.include_router(conversation_router, prefix="/api")

    mock_service = MagicMock()
    inner_app.dependency_overrides[get_conversation_service] = lambda: mock_service

    # 1. When conversation is finished and idle_evictable -> True
    conv = MagicMock()
    conv.execution_status = ConversationExecutionStatus.FINISHED
    mock_service.get_conversation = AsyncMock(return_value=conv)
    mock_service.is_conversation_idle_evictable.return_value = True
    ev_service = MagicMock()
    ev_service.is_idle_evictable.return_value = True
    mock_service._event_services = {cid: ev_service}

    transport = httpx.ASGITransport(app=inner_app)
    real_async_client = httpx.AsyncClient

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *args, **kwargs: real_async_client(
            transport=transport, base_url="http://127.0.0.1"
        ),
    ):
        assert await reg._is_suspendable(cid, cont) is True

        # 2. When conversation is still running -> False
        conv.execution_status = ConversationExecutionStatus.RUNNING
        assert await reg._is_suspendable(cid, cont) is False

        # 3. When conversation does not exist (404) -> False
        mock_service.get_conversation = AsyncMock(
            side_effect=HTTPException(status_code=404, detail="Not found")
        )
        assert await reg._is_suspendable(cid, cont) is False
