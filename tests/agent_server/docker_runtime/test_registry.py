from __future__ import annotations

import asyncio
import threading
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.registry import (
    DockerConversationRegistry,
    RunningConversationContainer,
)


def _container(conversation_id: UUID) -> RunningConversationContainer:
    return RunningConversationContainer(
        host=f"http://127.0.0.1/{conversation_id}",
        api_key=None,
        container_id=f"container-{conversation_id}",
        image="test-image",
    )


@pytest.mark.asyncio
async def test_get_or_create_deduplicates_same_conversation_start(tmp_path):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    calls = 0

    def build(conversation_id: UUID) -> RunningConversationContainer:
        nonlocal calls
        calls += 1
        return _container(conversation_id)

    registry._build_container = build

    first, second = await asyncio.gather(
        registry.get_or_create(conversation_id),
        registry.get_or_create(conversation_id),
    )

    assert calls == 1
    assert first[0] is second[0]
    assert first[1] is True
    assert second[1] is False


@pytest.mark.asyncio
async def test_get_or_create_starts_different_conversations_concurrently(tmp_path):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    entered: set[UUID] = set()
    entered_lock = threading.Lock()
    release = threading.Event()
    cid_a = uuid4()
    cid_b = uuid4()

    def build(conversation_id: UUID) -> RunningConversationContainer:
        with entered_lock:
            entered.add(conversation_id)
        assert release.wait(timeout=5)
        return _container(conversation_id)

    registry._build_container = build

    task_a = asyncio.create_task(registry.get_or_create(cid_a))
    task_b = asyncio.create_task(registry.get_or_create(cid_b))

    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        with entered_lock:
            if entered == {cid_a, cid_b}:
                break
        await asyncio.sleep(0.01)

    with entered_lock:
        assert entered == {cid_a, cid_b}

    release.set()
    result_a, result_b = await asyncio.gather(task_a, task_b)
    assert result_a[0] is not result_b[0]
    assert result_a[1] is True
    assert result_b[1] is True


@pytest.mark.asyncio
async def test_startup_health_failure_cleans_started_container(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    container = _container(conversation_id)
    cleaned: list[str | None] = []

    def run_container(**kwargs) -> RunningConversationContainer:
        return container

    def fail_health(container: RunningConversationContainer, *, timeout: float) -> None:
        raise RuntimeError("health failed")

    def cleanup(target: RunningConversationContainer) -> None:
        cleaned.append(target.container_id)
        target.container_id = None

    registry._run_container = run_container
    registry._wait_for_health = fail_health
    monkeypatch.setattr(RunningConversationContainer, "cleanup", cleanup)

    with pytest.raises(RuntimeError, match="health failed"):
        await registry.get_or_create(conversation_id)

    assert cleaned == [f"container-{conversation_id}"]
    assert registry.get(conversation_id) is None


@pytest.mark.asyncio
async def test_failed_start_can_be_retried(tmp_path):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    calls = 0

    def build(conversation_id: UUID) -> RunningConversationContainer:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        return _container(conversation_id)

    registry._build_container = build

    with pytest.raises(RuntimeError, match="boom"):
        await registry.get_or_create(conversation_id)

    container, is_new = await registry.get_or_create(conversation_id)

    assert calls == 2
    assert container.container_id == f"container-{conversation_id}"
    assert is_new is True


def test_container_env_forces_inner_runtime_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_CONVERSATION_RUNTIME", "docker")
    registry = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path,
            conversation_container_forward_env=["OH_CONVERSATION_RUNTIME"],
        )
    )

    env = registry._container_env()

    assert env["OH_CONVERSATION_RUNTIME"] == "local"


@pytest.mark.asyncio
async def test_idle_cleanup_stops_inactive_container(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    container = _container(conversation_id)
    container.last_activity = 0
    registry._containers[conversation_id] = container
    cleaned: list[str | None] = []

    monkeypatch.setattr(registry, "_is_running", lambda *_: False)
    monkeypatch.setattr(
        RunningConversationContainer,
        "cleanup",
        lambda target: cleaned.append(target.container_id),
    )

    await registry._cleanup_idle_once(timeout=1)

    assert registry.get(conversation_id) is None
    assert cleaned == [f"container-{conversation_id}"]


@pytest.mark.asyncio
async def test_idle_cleanup_preserves_active_connection(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    container = _container(conversation_id)
    container.last_activity = 0
    container.active_connections = 1
    registry._containers[conversation_id] = container

    monkeypatch.setattr(
        registry,
        "_is_running",
        lambda *_: pytest.fail("active container should not be inspected"),
    )

    await registry._cleanup_idle_once(timeout=1)

    assert registry.get(conversation_id) is container


@pytest.mark.asyncio
async def test_idle_cleanup_preserves_running_conversation(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(Config(conversations_path=tmp_path))
    conversation_id = uuid4()
    container = _container(conversation_id)
    container.last_activity = 0
    registry._containers[conversation_id] = container

    monkeypatch.setattr(registry, "_is_running", lambda *_: True)

    await registry._cleanup_idle_once(timeout=1)

    assert registry.get(conversation_id) is container
    assert container.last_activity > 0
