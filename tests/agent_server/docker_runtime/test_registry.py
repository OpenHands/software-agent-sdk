import asyncio
import subprocess
import threading
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
)


def registry(tmp_path, monkeypatch) -> DockerConversationRegistry:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
        )
    )


def container(conversation_id: UUID) -> ConversationContainer:
    return ConversationContainer(
        host=f"http://127.0.0.1/{conversation_id}",
        api_key="inner-key",
        container_id=f"container-{conversation_id}",
    )


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


def test_container_command_is_hardened_and_mounts_only_its_state(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id = uuid4()
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
    assert result.host == "http://127.0.0.1:32123"
    assert ["--cap-drop", "ALL"] == command[
        command.index("--cap-drop") : command.index("--cap-drop") + 2
    ]
    assert "no-new-privileges" in command
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
