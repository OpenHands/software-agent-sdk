from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.registry import (
    DockerConversationRegistry,
    RunningConversationContainer,
)
from openhands.agent_server.models import ConversationRuntimeStatus


def _container(conversation_id: UUID) -> RunningConversationContainer:
    return RunningConversationContainer(
        host=f"http://127.0.0.1/{conversation_id}",
        api_key=None,
        container_id=f"container-{conversation_id}",
        image="test-image",
    )


def _persisted_conversation(registry: DockerConversationRegistry, cid: UUID) -> None:
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")


def test_runtime_info_does_not_provision_missing_runtime(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    cid = uuid4()
    _persisted_conversation(registry, cid)

    info = registry.runtime_info(cid)

    assert info.runtime_status == ConversationRuntimeStatus.MISSING
    assert info.can_resume is True
    assert registry.get(cid) is None


def test_runtime_info_distinguishes_available_and_ownership_lost(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    available = uuid4()
    _persisted_conversation(registry, available)
    registry._containers[available] = _container(available)
    assert (
        registry.runtime_info(available).runtime_status
        == ConversationRuntimeStatus.AVAILABLE
    )

    ownership_lost = uuid4()
    directory = registry.conversation_dir(ownership_lost)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")
    info = registry.runtime_info(ownership_lost)
    assert info.runtime_status == ConversationRuntimeStatus.OWNERSHIP_LOST
    assert info.can_resume is False


@pytest.mark.asyncio
async def test_runtime_info_retains_start_failure(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    cid = uuid4()
    _persisted_conversation(registry, cid)

    def fail(conversation_id: UUID) -> RunningConversationContainer:
        raise RuntimeError(f"container failed: {conversation_id}")

    registry._build_container = fail
    with pytest.raises(RuntimeError, match="container failed"):
        await registry.get_or_create(cid)

    info = registry.runtime_info(cid)
    assert info.runtime_status == ConversationRuntimeStatus.ERROR
    assert info.can_resume is True
    assert info.runtime_error is not None
    assert info.runtime_error.code == "runtime_start_failed"


@pytest.mark.asyncio
async def test_get_or_create_deduplicates_same_conversation_start(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
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
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
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
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    registry = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("outer-test-encryption"),
        )
    )
    conversation_id = uuid4()
    await registry.prepare(conversation_id)
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
    await registry.shutdown()


@pytest.mark.asyncio
async def test_failed_start_can_be_retried(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
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


@pytest.mark.asyncio
async def test_build_container_mounts_dedicated_writable_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    conversations_path = tmp_path / "conversations"
    workspace_path = tmp_path / "workspaces"
    registry = DockerConversationRegistry(
        Config(
            conversations_path=conversations_path,
            workspace_path=workspace_path,
            secret_key=SecretStr("outer-test-encryption"),
        )
    )
    captured_volumes: list[str] = []

    def run_container(**kwargs) -> RunningConversationContainer:
        captured_volumes.extend(kwargs["volumes"])
        return _container(kwargs["conversation_id"])

    monkeypatch.setattr(registry, "_run_container", run_container)
    monkeypatch.setattr(registry, "_wait_for_health", lambda *args, **kwargs: None)

    conversation_id = uuid4()
    await registry.prepare(conversation_id)
    registry._build_container(conversation_id)

    assert (
        registry.provisioning.runtime_dir(conversation_id).stat().st_mode & 0o777
        == 0o700
    )
    host_workspace = workspace_path.resolve() / conversation_id.hex
    assert host_workspace.is_dir()
    assert f"{host_workspace}:/workspace" in captured_volumes
    assert not any(
        volume.startswith(str(tmp_path / "global") + ":") for volume in captured_volumes
    )
    await registry.shutdown()


def test_run_container_uses_host_identity_for_bind_mounts(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    commands: list[list[str]] = []

    def execute(command, **kwargs):
        commands.append(command)
        stdout = "test-container\n" if command[:3] == ["docker", "run", "-d"] else ""
        if command[:2] == ["docker", "port"]:
            stdout = "127.0.0.1:32123\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.subprocess.run", execute
    )

    container = registry._run_container(
        conversation_id=uuid4(),
        image="test-image",
        platform="linux/amd64",
        volumes=["/host/path:/container/path"],
        env={},
        network=None,
        api_key=None,
    )

    run_command = commands[1]
    user_flag = run_command.index("--user")
    assert run_command[user_flag + 1] == f"{os.getuid()}:{os.getgid()}"
    assert container.container_id == "test-container"
    assert run_command[run_command.index("-p") + 1] == "127.0.0.1::8000"
    assert container.host == "http://127.0.0.1:32123"


def test_run_container_applies_ownership_and_security_policy(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path,
            conversation_container_memory="2g",
            conversation_container_cpus=2.5,
            conversation_container_pids_limit=256,
        )
    )
    commands: list[list[str]] = []

    def execute(command, **kwargs):
        commands.append(command)
        stdout = "test-container\n" if command[:3] == ["docker", "run", "-d"] else ""
        if command[:2] == ["docker", "port"]:
            stdout = "127.0.0.1:32123\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.subprocess.run", execute
    )

    conversation_id = uuid4()
    registry._run_container(
        conversation_id=conversation_id,
        image="test-image",
        platform="linux/amd64",
        volumes=[],
        env={},
        network=None,
        api_key=None,
    )

    run_command = commands[1]
    assert ["--cap-drop", "ALL"] == run_command[
        run_command.index("--cap-drop") : run_command.index("--cap-drop") + 2
    ]
    assert ["--security-opt", "no-new-privileges"] == run_command[
        run_command.index("--security-opt") : run_command.index("--security-opt") + 2
    ]
    assert run_command[run_command.index("--memory") + 1] == "2g"
    assert run_command[run_command.index("--cpus") + 1] == "2.5"
    assert run_command[run_command.index("--pids-limit") + 1] == "256"
    labels = [
        run_command[index + 1]
        for index, value in enumerate(run_command)
        if value == "--label"
    ]
    assert f"ai.openhands.conversation-id={conversation_id}" in labels
    assert f"ai.openhands.runtime-owner={registry.execution_scope}" in labels


def test_cleanup_stale_containers_is_scoped_to_registry_owner(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    commands: list[list[str]] = []

    def execute(command, **kwargs):
        commands.append(command)
        if command[:3] == ["docker", "ps", "-aq"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="owned-a\nowned-b\n", stderr=""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.subprocess.run", execute
    )

    registry.cleanup_stale_containers()

    assert commands[0] == [
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"label=ai.openhands.runtime-owner={registry.execution_scope}",
    ]
    assert commands[1] == ["docker", "rm", "-f", "owned-a", "owned-b"]


def test_container_env_forces_inner_runtime_to_local(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_CONVERSATION_RUNTIME", "docker")
    registry = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path,
        )
    )

    env = registry._container_env()

    assert env["OH_CONVERSATION_RUNTIME"] == "local"


def test_docker_launch_preserves_explicit_credentials(tmp_path, monkeypatch):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "if sys.argv[1] == 'run':\n"
        "    assert os.environ.get('OH_SECRET_KEY') == 'test-cipher'\n"
        "    assert os.environ.get('OH_SESSION_API_KEYS_0') == 'test-session'\n"
        "    assert 'OH_SESSION_API_KEYS_1' not in os.environ\n"
        "    assert sys.argv[sys.argv.index('-p') + 1] == '127.0.0.1::8000'\n"
        "    print('test-container')\n"
        "elif sys.argv[1] == 'port':\n"
        "    print('127.0.0.1:32123')\n"
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("OH_SESSION_API_KEYS_1", "not-forwarded")
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    container = registry._run_container(
        conversation_id=uuid4(),
        image="test-image",
        platform="linux/amd64",
        volumes=[],
        env={"OH_SECRET_KEY": "test-cipher", "OH_SESSION_API_KEYS_0": "test-session"},
        network=None,
        api_key="test-session",
    )
    assert container.container_id == "test-container"


@pytest.mark.asyncio
async def test_cancelled_start_waiter_does_not_cancel_other_waiters(tmp_path):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    cid = uuid4()
    entered = threading.Event()
    release = threading.Event()

    def build(conversation_id):
        entered.set()
        assert release.wait(timeout=5)
        return _container(conversation_id)

    registry._build_container = build
    first = asyncio.create_task(registry.get_or_create(cid))
    second = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        second = asyncio.create_task(registry.get_or_create(cid))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        result = await asyncio.gather(second, return_exceptions=True)
        assert not isinstance(result[0], BaseException), (
            "A disconnected or timed-out caller cancelled another startup waiter"
        )
        assert registry.get(cid) is result[0][0]
    finally:
        release.set()
        await asyncio.gather(
            first, *([second] if second else []), return_exceptions=True
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_lone_cancelled_waiter_can_recover_or_shutdown(
    tmp_path, recover, monkeypatch
):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    cid = uuid4()
    entered = threading.Event()
    release = threading.Event()
    cleaned = []

    def build(conversation_id):
        entered.set()
        assert release.wait(timeout=5)
        return _container(conversation_id)

    registry._build_container = build
    monkeypatch.setattr(
        RunningConversationContainer,
        "cleanup",
        lambda container: cleaned.append(container),
    )
    waiter = asyncio.create_task(registry.get_or_create(cid))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        release.set()
        if recover:
            container, _ = await registry.get_or_create(cid)
            assert registry.get(cid) is container
        await registry.shutdown()
        assert len(cleaned) == 1
        assert registry.get(cid) is None
    finally:
        release.set()
        await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.parametrize("binding", ["", "0.0.0.0:32123", "127.0.0.1:0"])
def test_invalid_assigned_port_cleans_up_container(tmp_path, monkeypatch, binding):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        stdout = "test-container\n" if command[:2] == ["docker", "run"] else ""
        if command[:2] == ["docker", "port"]:
            stdout = binding
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.execute_command", execute
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.subprocess.run", execute
    )
    with pytest.raises(ValueError):
        registry._run_container(
            conversation_id=uuid4(),
            image="test-image",
            platform="linux/amd64",
            volumes=[],
            env={},
            network=None,
            api_key=None,
        )
    assert ["docker", "stop", "test-container"] in commands


def test_failed_docker_run_surfaces_stderr(tmp_path, monkeypatch):
    registry = DockerConversationRegistry(
        Config(conversations_path=tmp_path, secret_key=SecretStr("test-key"))
    )

    def execute(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 125, stdout="", stderr="image pull failed"
        )

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.registry.subprocess.run", execute
    )
    with pytest.raises(RuntimeError, match="image pull failed"):
        registry._run_container(
            conversation_id=uuid4(),
            image="test-image",
            platform="linux/amd64",
            volumes=[],
            env={},
            network=None,
            api_key=None,
        )


def test_forwarded_environment_rejects_unsupported_names(tmp_path):
    with pytest.raises(ValidationError):
        Config.model_validate({"conversation_container_forward_env": ["OH_SECRET_KEY"]})
    assert (
        Config(conversation_container_forward_env=[]).conversation_container_forward_env
        == []
    )


def test_cleanup_failure_preserves_container_identity(monkeypatch):
    from subprocess import CompletedProcess

    from openhands.agent_server.docker_runtime import registry as module

    container = _container(uuid4())
    original = container.container_id
    monkeypatch.setattr(
        module, "execute_command", lambda args: CompletedProcess(args, 1, "", "failed")
    )
    with pytest.raises(RuntimeError, match="Failed to stop"):
        container.cleanup()
    assert container.container_id == original
