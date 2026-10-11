import asyncio
import json
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
)
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.storage import trash as trash_module
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def registry(
    tmp_path, monkeypatch, idle_ttl: float | None = 1200, **config
) -> DockerConversationRegistry:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
            conversation_idle_ttl_seconds=idle_ttl,
            **config,
        )
    )


def container(conversation_id: UUID) -> ConversationContainer:
    return ConversationContainer(
        host=f"http://127.0.0.1/{conversation_id}",
        api_key="inner-key",
        container_id=f"container-{conversation_id}",
    )


def set_execution_status(
    runtime: DockerConversationRegistry, status: ConversationExecutionStatus
) -> AsyncMock:
    service = AsyncMock(spec=ConversationService)
    service.get_conversation.return_value = SimpleNamespace(execution_status=status)
    runtime.configure_service(cast(ConversationService, service))
    return service


def test_missing_container_is_already_stopped(monkeypatch):
    missing = container(uuid4())
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 1, stdout="", stderr="No such container"
        ),
    )

    missing.stop()


@pytest.mark.asyncio
async def test_docker_catalog_lists_legacy_local_and_isolated_conversations(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    conversations_dir = runtime.config.conversations_path

    async def persist(conversation_id, cipher, workspace_name):
        workspace = tmp_path / workspace_name
        workspace.mkdir()
        request = StartConversationRequest(
            conversation_id=conversation_id,
            agent=Agent(
                llm=LLM(
                    model="gpt-4o",
                    usage_id="test-llm",
                    api_key=SecretStr(f"secret-{workspace_name}"),
                ),
                tools=[],
            ),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            confirmation_policy=NeverConfirm(),
        )
        async with ConversationService(
            conversations_dir=conversations_dir, cipher=cipher
        ) as service:
            await service.start_conversation(request)
            events = await service.get_event_service(conversation_id)
            assert events is not None
            await events.send_message(
                Message(role="user", content=[TextContent(text=workspace_name)])
            )

    legacy_id = uuid4()
    await persist(legacy_id, runtime.provisioning.cipher, "legacy-workspace")

    docker_id = uuid4()
    identity = runtime.provisioning.create(docker_id)
    await persist(docker_id, identity.cipher, "docker-workspace")

    service = ConversationService(
        conversations_dir=conversations_dir,
        cipher=runtime.provisioning.cipher,
    )
    runtime.configure_service(service)
    async with service:
        page = await service.search_conversations()
        persisted_events = await service.get_persisted_event_service(docker_id)
        assert persisted_events is not None
        persisted_page = await persisted_events.search_events(body="docker-workspace")
        legacy_events = await service.get_event_service(legacy_id)
        docker_events = await service.get_event_service(docker_id)

    assert {item.id for item in page.items} == {legacy_id, docker_id}
    assert len(persisted_page.items) == 1
    assert legacy_events is not None
    assert legacy_events.cipher is not None
    assert legacy_events.cipher.secret_key == runtime.provisioning.cipher.secret_key
    assert docker_events is not None
    assert docker_events.cipher is not None
    assert docker_events.cipher.secret_key == identity.cipher.secret_key
    assert (
        runtime.resolve_persisted_cipher(legacy_id).secret_key
        == runtime.provisioning.cipher.secret_key
    )
    assert (
        runtime.resolve_persisted_cipher(docker_id).secret_key
        == identity.cipher.secret_key
    )


def test_present_invalid_identity_does_not_fall_back_to_host_cipher(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    runtime.provisioning.manifest_path(conversation_id).write_text("not-json")

    with pytest.raises(ValueError):
        runtime.resolve_persisted_cipher(conversation_id)


@pytest.mark.asyncio
async def test_same_conversation_shares_one_start(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    calls = 0

    def build(conversation_id: UUID):
        nonlocal calls
        calls += 1
        return container(conversation_id)

    runtime._build_container = build
    first, second = await asyncio.gather(
        runtime.get_or_create(conversation_id),
        runtime.get_or_create(conversation_id),
    )
    assert calls == 1
    assert first is second


@pytest.mark.asyncio
async def test_different_conversations_start_concurrently(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    entered = set()
    release = threading.Event()

    def build(conversation_id: UUID):
        entered.add(conversation_id)
        assert release.wait(5)
        return container(conversation_id)

    runtime._build_container = build
    ids = [uuid4(), uuid4()]
    tasks = [asyncio.create_task(runtime.get_or_create(cid)) for cid in ids]
    while len(entered) < 2:
        await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(*tasks)
    assert entered == set(ids)


@pytest.mark.asyncio
async def test_stale_cached_container_is_replaced(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    stale = container(conversation_id)
    fresh = ConversationContainer("http://fresh", "fresh-key", "fresh-container")
    runtime._containers[conversation_id] = stale
    monkeypatch.setattr(ConversationContainer, "is_running", lambda _self: False)
    runtime._build_container = lambda conversation_id: fresh

    assert await runtime.get_or_create(conversation_id) is fresh
    assert runtime.get(conversation_id) is fresh


@pytest.mark.asyncio
async def test_terminal_idle_runtime_is_stopped(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    runtime._containers[conversation_id] = container(conversation_id)
    runtime._last_access[conversation_id] = 0
    service = set_execution_status(runtime, ConversationExecutionStatus.FINISHED)
    stopped = []
    monkeypatch.setattr(
        ConversationContainer,
        "stop",
        lambda self: stopped.append(self.container_id),
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: 20
    )

    await runtime._evict_idle_runtimes(10)

    assert runtime.get(conversation_id) is None
    assert stopped == [f"container-{conversation_id}"]
    service.refresh_persisted_conversation.assert_awaited_once_with(conversation_id)


@pytest.mark.asyncio
async def test_running_idle_runtime_is_retained(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    active = container(conversation_id)
    runtime._containers[conversation_id] = active
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.RUNNING)
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: 20
    )

    await runtime._evict_idle_runtimes(10)

    assert runtime.get(conversation_id) is active


@pytest.mark.asyncio
async def test_attached_session_prevents_idle_eviction(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    active = container(conversation_id)
    runtime._containers[conversation_id] = active
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.FINISHED)
    stopped = []
    monkeypatch.setattr(
        ConversationContainer,
        "stop",
        lambda self: stopped.append(self.container_id),
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: 100
    )
    runtime.attach_session(conversation_id)

    await runtime._evict_idle_runtimes(10)

    assert runtime.get(conversation_id) is active
    assert stopped == []


@pytest.mark.asyncio
async def test_idle_runtime_is_evicted_after_session_detaches(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    active = container(conversation_id)
    runtime._containers[conversation_id] = active
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.FINISHED)
    stopped = []
    monkeypatch.setattr(
        ConversationContainer,
        "stop",
        lambda self: stopped.append(self.container_id),
    )
    now = 100.0
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: now
    )
    runtime.attach_session(conversation_id)

    await runtime._evict_idle_runtimes(10)
    assert runtime.get(conversation_id) is active

    runtime.detach_session(conversation_id)
    now = 111.0
    await runtime._evict_idle_runtimes(10)

    assert runtime.get(conversation_id) is None
    assert stopped == [active.container_id]


@pytest.mark.asyncio
async def test_runtime_access_refreshes_idle_deadline(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    active = container(conversation_id)
    runtime._containers[conversation_id] = active
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.FINISHED)
    stopped = []
    monkeypatch.setattr(ConversationContainer, "is_running", lambda _self: True)
    monkeypatch.setattr(
        ConversationContainer,
        "stop",
        lambda self: stopped.append(self.container_id),
    )
    now = 20.0
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: now
    )

    assert await runtime.get_or_create(conversation_id) is active
    now = 25
    await runtime._evict_idle_runtimes(10)
    assert runtime.get(conversation_id) is active
    now = 31
    await runtime._evict_idle_runtimes(10)
    assert runtime.get(conversation_id) is None
    assert stopped == [active.container_id]


@pytest.mark.asyncio
async def test_disabled_idle_ttl_does_not_start_eviction(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch, idle_ttl=None)
    monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)

    await runtime.start()

    assert runtime._eviction_task is None


@pytest.mark.asyncio
async def test_shutdown_cancels_eviction_and_stops_containers(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    active = container(conversation_id)
    runtime._containers[conversation_id] = active
    runtime._last_access[conversation_id] = 0
    stopped = []
    monkeypatch.setattr(
        ConversationContainer,
        "stop",
        lambda self: stopped.append(self.container_id),
    )

    async def wait_forever() -> None:
        await asyncio.Event().wait()

    runtime._eviction_task = asyncio.create_task(wait_forever())

    await runtime.shutdown()

    assert runtime._eviction_task is None
    assert runtime.get(conversation_id) is None
    assert stopped == [active.container_id]


def _build_container(runtime, monkeypatch, conversation_id):
    runtime.provisioning.create(conversation_id)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(
            command, 0, stdout="container-id\n", stderr=""
        )

    def execute(command):
        if command[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="127.0.0.1:32123\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(runtime, "_wait_until_ready", lambda container: None)

    result = runtime._build_container(conversation_id)
    command, env = commands[0]
    return result, command, env


def test_container_command_is_hardened_and_mounts_only_its_state(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()

    result, command, env = _build_container(runtime, monkeypatch, conversation_id)
    assert result.host == "http://127.0.0.1:32123"
    assert ["--cap-drop", "ALL"] == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in command
    assert "host.docker.internal:host-gateway" in command
    assert "127.0.0.1::8000" in command
    mounts = [command[index + 1] for index, item in enumerate(command) if item == "-v"]
    assert len(mounts) == 3
    assert all(
        conversation_id.hex in mount or mount.endswith(":/workspace")
        for mount in mounts
    )
    assert env["HOME"] == "/var/openhands/.openhands"
    assert ["-e", "HOME"] == command[
        command.index("HOME") - 1 : command.index("HOME") + 1
    ]
    assert env["OH_SECRET_KEY"] != "outer-key"


def test_container_command_does_not_forward_unset_observability_vars(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    for name in (
        "LMNR_PROJECT_API_KEY",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OPENHANDS_OBSERVABILITY_METADATA",
    ):
        monkeypatch.delenv(name, raising=False)

    _, command, env = _build_container(runtime, monkeypatch, uuid4())

    assert "LMNR_PROJECT_API_KEY" not in env
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" not in env


def test_container_command_forwards_observability_vars(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    monkeypatch.setenv("LMNR_PROJECT_API_KEY", "laminar-key")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:4317")

    _, command, env = _build_container(runtime, monkeypatch, uuid4())

    assert env["LMNR_PROJECT_API_KEY"] == "laminar-key"
    assert env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] == "http://localhost:4317"
    for name in ("LMNR_PROJECT_API_KEY", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        index = command.index(name)
        assert ["-e", name] == command[index - 1 : index + 1]


def seed_runtime(
    runtime: DockerConversationRegistry,
    status: ConversationExecutionStatus | None = ConversationExecutionStatus.FINISHED,
    npm: bool = False,
) -> tuple[UUID, dict[Path, bytes]]:
    """Provision a runtime with a populated cache and the state that must survive.

    Returns the conversation id and a snapshot of every kept file's contents.
    """
    conversation_id = uuid4()
    runtime.provisioning.create(conversation_id)
    runtime_dir = runtime.provisioning.runtime_dir(conversation_id)
    cache = runtime_dir / "persistence" / ".cache" / "uv" / "wheels-v5"
    cache.mkdir(parents=True)
    (cache / "requests.whl").write_bytes(b"wheel")
    if npm:
        npm_cache = runtime_dir / "persistence" / ".npm" / "_cacache"
        npm_cache.mkdir(parents=True)
        (npm_cache / "index").write_bytes(b"left-pad")
    kept = {
        runtime_dir / "persistence" / ".bash_history": b"uv pip install requests",
        runtime_dir / "workspace" / ".venv" / "pyvenv.cfg": b"home = /usr",
        runtime_dir / "workspace" / "main.py": b"print('hi')",
        runtime.conversation_dir(conversation_id) / "events" / "event-0.json": b"{}",
    }
    if status is not None:
        base_state = runtime.conversation_dir(conversation_id) / "base_state.json"
        kept[base_state] = json.dumps({"execution_status": status.value}).encode()
    for path, content in kept.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = runtime.provisioning.manifest_path(conversation_id)
    kept[manifest] = manifest.read_bytes()
    return conversation_id, kept


def cache_dir(
    runtime: DockerConversationRegistry, conversation_id: UUID, name: str = ".cache"
) -> Path:
    return runtime.provisioning.runtime_dir(conversation_id) / "persistence" / name


def in_trash(runtime: DockerConversationRegistry) -> list[Path]:
    trash = runtime.reclaimer.trash.dir
    return list(trash.iterdir()) if trash.exists() else []


def assert_kept(kept: dict[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in kept} == kept


async def reclaimed(runtime: DockerConversationRegistry) -> None:
    await runtime.reclaimer.trash.drain()


@pytest.fixture
def stopped_containers(monkeypatch) -> list[str]:
    stopped: list[str] = []
    monkeypatch.setattr(
        ConversationContainer, "stop", lambda self: stopped.append(self.container_id)
    )
    return stopped


@pytest.fixture
def slow_remove(monkeypatch) -> Iterator[SimpleNamespace]:
    """Hold every cache deletion until ``release`` is set."""
    real_remove = trash_module._remove
    gate = SimpleNamespace(
        started=threading.Event(), release=threading.Event(), removed=threading.Event()
    )

    def remove(path: Path) -> None:
        gate.started.set()
        assert gate.release.wait(5)
        real_remove(path)
        gate.removed.set()

    monkeypatch.setattr(trash_module, "_remove", remove)
    yield gate
    gate.release.set()


@pytest.mark.asyncio
async def test_idle_eviction_prunes_cache_and_keeps_conversation_state(
    tmp_path, monkeypatch, stopped_containers
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime)
    runtime._containers[conversation_id] = container(conversation_id)
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.FINISHED)
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: 20
    )

    await runtime._evict_idle_runtimes(10)
    await reclaimed(runtime)

    assert stopped_containers == [f"container-{conversation_id}"]
    assert not cache_dir(runtime, conversation_id).exists()
    assert_kept(kept)
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_running_idle_runtime_keeps_its_cache(
    tmp_path, monkeypatch, stopped_containers
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime, ConversationExecutionStatus.RUNNING)
    runtime._containers[conversation_id] = container(conversation_id)
    runtime._last_access[conversation_id] = 0
    set_execution_status(runtime, ConversationExecutionStatus.RUNNING)
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.time.monotonic", lambda: 20
    )

    await runtime._evict_idle_runtimes(10)
    await reclaimed(runtime)

    assert stopped_containers == []
    assert cache_dir(runtime, conversation_id).is_dir()
    assert_kept(kept)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    # RUNNING on disk is stale once the container is gone (e.g. a stop that
    # escalated to SIGKILL mid-run), so it must not pin the cache forever.
    [*ConversationExecutionStatus, None],
)
async def test_explicit_stop_prunes_cache(
    tmp_path, monkeypatch, stopped_containers, status
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime, status)
    runtime._containers[conversation_id] = container(conversation_id)

    await runtime.stop(conversation_id)
    await reclaimed(runtime)

    assert stopped_containers == [f"container-{conversation_id}"]
    assert not cache_dir(runtime, conversation_id).exists()
    assert_kept(kept)
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_stop_prunes_npm_cache_too(tmp_path, monkeypatch, stopped_containers):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime, npm=True)
    runtime._containers[conversation_id] = container(conversation_id)

    await runtime.stop(conversation_id)
    await reclaimed(runtime)

    assert not cache_dir(runtime, conversation_id).exists()
    assert not cache_dir(runtime, conversation_id, ".npm").exists()
    assert_kept(kept)
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_startup_prunes_npm_cache_too(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime, npm=True)

    await runtime.reclaimer.start()
    await reclaimed(runtime)

    assert not cache_dir(runtime, conversation_id).exists()
    assert not cache_dir(runtime, conversation_id, ".npm").exists()
    assert_kept(kept)


@pytest.mark.asyncio
async def test_stop_returns_before_cache_deletion_finishes(
    tmp_path, monkeypatch, stopped_containers, slow_remove
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = seed_runtime(runtime)
    runtime._containers[conversation_id] = container(conversation_id)

    await runtime.stop(conversation_id)

    # Detached from the mount already, but not yet deleted.
    assert not cache_dir(runtime, conversation_id).exists()
    assert len(in_trash(runtime)) == 1
    slow_remove.release.set()
    await reclaimed(runtime)
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_failed_stop_keeps_cache(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = seed_runtime(runtime)
    runtime._containers[conversation_id] = container(conversation_id)

    def fail(_self):
        raise RuntimeError("docker stop failed")

    monkeypatch.setattr(ConversationContainer, "stop", fail)

    with pytest.raises(RuntimeError):
        await runtime.stop(conversation_id)

    assert cache_dir(runtime, conversation_id).is_dir()


@pytest.mark.asyncio
async def test_prune_skips_runtime_that_restarted_after_stop(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = seed_runtime(runtime)
    runtime._containers[conversation_id] = container(conversation_id)

    await runtime.reclaimer.on_stop(conversation_id)
    await reclaimed(runtime)

    assert cache_dir(runtime, conversation_id).is_dir()


@pytest.mark.asyncio
async def test_prune_skips_runtime_being_deleted(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = seed_runtime(runtime)
    assert await runtime.begin_delete(conversation_id)

    await runtime.reclaimer.on_stop(conversation_id)
    await reclaimed(runtime)

    # The DELETE route owns this runtime's files now.
    assert cache_dir(runtime, conversation_id).is_dir()


@pytest.mark.asyncio
async def test_prune_is_idempotent_when_cache_is_absent(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime)

    await runtime.reclaimer.on_stop(conversation_id)
    await runtime.reclaimer.on_stop(conversation_id)
    await reclaimed(runtime)

    assert not cache_dir(runtime, conversation_id).exists()
    assert_kept(kept)
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_prune_unlinks_symlinked_cache_without_following_it(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = seed_runtime(runtime)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    cache = cache_dir(runtime, conversation_id)
    shutil.rmtree(cache)
    cache.symlink_to(outside, target_is_directory=True)

    await runtime.reclaimer.on_stop(conversation_id)
    await reclaimed(runtime)

    assert not cache.is_symlink() and not cache.exists()
    assert (outside / "precious").read_text() == "keep"
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_startup_prunes_every_runtime_cache(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)
    seeded = [
        seed_runtime(runtime, status)
        for status in (
            ConversationExecutionStatus.FINISHED,
            None,
            # Left RUNNING by a crash; startup has removed every container.
            ConversationExecutionStatus.RUNNING,
        )
    ]
    unrelated = runtime.provisioning.data_root / "not-a-conversation" / ".cache"
    unrelated.mkdir(parents=True)

    await runtime.start()
    await reclaimed(runtime)
    await runtime.shutdown()

    for conversation_id, kept in seeded:
        assert not cache_dir(runtime, conversation_id).exists()
        assert_kept(kept)
    assert unrelated.is_dir()
    assert in_trash(runtime) == []

    # A second pass, with the caches already gone, changes nothing.
    await runtime.reclaimer.start()
    await reclaimed(runtime)
    for _, kept in seeded:
        assert_kept(kept)


@pytest.mark.asyncio
async def test_startup_does_not_wait_for_cache_deletion(
    tmp_path, monkeypatch, slow_remove
):
    runtime = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)
    conversation_id, _ = seed_runtime(runtime)

    await runtime.start()

    assert not cache_dir(runtime, conversation_id).exists()
    assert len(in_trash(runtime)) == 1
    slow_remove.release.set()
    await reclaimed(runtime)
    assert in_trash(runtime) == []
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_shutdown_abandons_deletions_and_next_start_sweeps_them(
    tmp_path, monkeypatch, slow_remove
):
    runtime = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)
    for _ in range(2):
        seed_runtime(runtime)

    await runtime.start()
    await asyncio.to_thread(slow_remove.started.wait, 5)
    await runtime.shutdown()

    assert runtime.reclaimer.trash._emptying is None
    slow_remove.release.set()
    # The in-flight deletion finishes; the queued one was abandoned.
    assert await asyncio.to_thread(slow_remove.removed.wait, 5)
    assert len(in_trash(runtime)) == 1

    restarted = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(restarted, "cleanup_stale_containers", lambda: None)
    await restarted.start()
    await reclaimed(restarted)
    await restarted.shutdown()
    assert in_trash(restarted) == []


@pytest.mark.asyncio
async def test_startup_prune_continues_past_a_broken_runtime(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    broken_id, _ = seed_runtime(runtime)
    healthy_id, _ = seed_runtime(runtime)
    persistence = runtime.provisioning.runtime_dir(broken_id) / "persistence"
    shutil.move(persistence, tmp_path / "moved-persistence")
    persistence.symlink_to(tmp_path / "moved-persistence", target_is_directory=True)

    await runtime.reclaimer.start()
    await reclaimed(runtime)

    assert (tmp_path / "moved-persistence" / ".cache").is_dir()
    assert not cache_dir(runtime, healthy_id).exists()
    assert in_trash(runtime) == []


@pytest.mark.asyncio
async def test_startup_prune_skips_runtime_deleted_mid_listing(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    # No state file, so its age falls back to the runtime dir's own mtime.
    gone_id, _ = seed_runtime(runtime, status=None)
    healthy_id, _ = seed_runtime(runtime)
    gone_dir = runtime.provisioning.runtime_dir(gone_id)
    real_is_dir = Path.is_dir

    def is_dir(path: Path, **kwargs) -> bool:
        exists = real_is_dir(path, **kwargs)
        if exists and path == gone_dir:
            # A concurrent DELETE removes it right after the check.
            shutil.rmtree(path)
        return exists

    monkeypatch.setattr(Path, "is_dir", is_dir)

    await runtime.reclaimer.start()
    await reclaimed(runtime)

    assert not gone_dir.exists()
    assert not cache_dir(runtime, healthy_id).exists()


@pytest.mark.asyncio
async def test_resume_after_prune_mounts_a_fresh_cache_location(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, kept = seed_runtime(runtime)
    await runtime.reclaimer.start()
    await reclaimed(runtime)
    commands = capture_docker_run(runtime, monkeypatch)

    runtime._build_container(conversation_id)

    persistence = runtime.provisioning.runtime_dir(conversation_id) / "persistence"
    assert f"{persistence}:/var/openhands/.openhands" in mounts(commands[0][0])
    assert not (persistence / ".cache").exists()
    assert_kept(kept)


def capture_docker_run(
    runtime: DockerConversationRegistry, monkeypatch
) -> list[tuple[list[str], dict[str, str]]]:
    commands: list[tuple[list[str], dict[str, str]]] = []

    def run(command, **kwargs):
        commands.append((command, kwargs["env"]))
        return subprocess.CompletedProcess(
            command, 0, stdout="container-id\n", stderr=""
        )

    def execute(command):
        if command[:2] == ["docker", "port"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="127.0.0.1:32123\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")

    monkeypatch.setattr("subprocess.run", run)
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(runtime, "_wait_until_ready", lambda container: None)
    return commands


def mounts(command: list[str]) -> list[str]:
    return [
        command[index + 1]
        for index, item in enumerate(command)
        if item in ("-v", "--mount")
    ]


def test_container_writes_no_core_dumps(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
    runtime.provisioning.create(conversation_id)
    commands = capture_docker_run(runtime, monkeypatch)

    runtime._build_container(conversation_id)

    command, _ = commands[0]
    assert ["--ulimit", "core=0"] == command[
        command.index("--ulimit") : command.index("--ulimit") + 2
    ]


@pytest.mark.parametrize(
    ("image_has_browser", "enable_browser", "flag"),
    [(None, True, "1"), (False, True, "0"), (True, False, "0")],
)
def test_container_gets_the_host_browser_setting(
    tmp_path, monkeypatch, image_has_browser, enable_browser, flag
):
    runtime = registry(
        tmp_path,
        monkeypatch,
        conversation_image_has_browser=image_has_browser,
        enable_browser=enable_browser,
    )

    _, command, env = _build_container(runtime, monkeypatch, uuid4())

    assert env["OH_ENABLE_BROWSER"] == flag
    assert ["-e", "OH_ENABLE_BROWSER"] == command[
        command.index("OH_ENABLE_BROWSER") - 1 : command.index("OH_ENABLE_BROWSER") + 1
    ]
