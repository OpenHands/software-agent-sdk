import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime import routers
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
    RuntimeRetiredError,
)
from openhands.agent_server.models import ConversationRuntimeStatus


DAY = 86400


def registry(
    tmp_path, monkeypatch, days: float | None = 7
) -> DockerConversationRegistry:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
            conversation_runtime_retention_days=days,
        )
    )


def provision(
    runtime: DockerConversationRegistry,
    days_inactive: float,
    workspace: Path | None = None,
) -> tuple[UUID, Path]:
    conversation_id = uuid4()
    identity = runtime.provisioning.create(conversation_id, workspace)
    runtime_dir = runtime.provisioning.runtime_dir(conversation_id)
    (runtime_dir / "persistence").mkdir(parents=True, exist_ok=True)
    (identity.workspace_path / "main.py").write_text("print('hi')")
    history = runtime.conversation_dir(conversation_id)
    (history / "events").mkdir(parents=True, exist_ok=True)
    (history / "events" / "event-0.json").write_text("{}")
    state = history / "base_state.json"
    state.write_text("{}")
    last = time.time() - days_inactive * DAY
    os.utime(state, (last, last))
    return conversation_id, identity.workspace_path


@pytest.mark.asyncio
async def test_inactive_runtime_is_retired_and_history_kept(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=8)
    persisted = runtime.resolve_persisted_cipher(conversation_id).encrypt(
        SecretStr("llm-key")
    )

    await runtime._enforce_retention()

    assert not runtime.provisioning.runtime_dir(conversation_id).exists()
    assert not workspace.exists()
    history = runtime.conversation_dir(conversation_id)
    assert (history / "events" / "event-0.json").is_file()
    # The manifest stays: without its key the history could not be decrypted.
    decrypted = runtime.resolve_persisted_cipher(conversation_id).decrypt(persisted)
    assert decrypted is not None and decrypted.get_secret_value() == "llm-key"
    info = runtime.runtime_info(conversation_id)
    assert info.runtime_status == ConversationRuntimeStatus.MISSING
    assert info.can_resume is False
    assert list(runtime.provisioning.data_root.glob(".cache-pruned-*")) == []


@pytest.mark.asyncio
async def test_recent_runtime_is_kept(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=6)

    await runtime._enforce_retention()

    assert (workspace / "main.py").is_file()
    assert runtime.runtime_info(conversation_id).can_resume is True


@pytest.mark.asyncio
async def test_live_runtime_is_kept_however_old(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=30)
    runtime._containers[conversation_id] = ConversationContainer(
        host="http://127.0.0.1", api_key="k", container_id="c"
    )

    await runtime._enforce_retention()

    assert (workspace / "main.py").is_file()
    assert not runtime.is_retired(conversation_id)


@pytest.mark.asyncio
async def test_caller_supplied_workspace_survives_retirement(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    checkout = tmp_path / "user-checkout"
    checkout.mkdir()
    conversation_id, workspace = provision(runtime, days_inactive=8, workspace=checkout)

    await runtime._enforce_retention()

    assert runtime.is_retired(conversation_id)
    assert (checkout / "main.py").is_file()


@pytest.mark.asyncio
async def test_retired_runtime_cannot_be_resumed(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = provision(runtime, days_inactive=8)
    await runtime._enforce_retention()

    with pytest.raises(RuntimeRetiredError):
        await runtime.get_or_create(conversation_id)
    with pytest.raises(HTTPException) as raised:
        await routers._container(runtime, conversation_id)
    assert raised.value.status_code == 410


@pytest.mark.asyncio
async def test_interrupted_retirement_is_finished_by_the_next_pass(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = provision(runtime, days_inactive=8)
    # A crash after the marker, before the runtime dir was moved.
    runtime.retired_marker(conversation_id).touch()

    await runtime._enforce_retention()

    assert not runtime.provisioning.runtime_dir(conversation_id).exists()


def test_startup_sweep_deletes_runtimes_retired_before_a_crash(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    leftover = runtime.provisioning.data_root / ".cache-pruned-x"
    (leftover / "workspace").mkdir(parents=True)

    assert leftover in runtime.detach_stopped_caches()


@pytest.mark.asyncio
async def test_retention_loop_runs_only_when_configured(tmp_path, monkeypatch):
    for days, expected in ((7, True), (None, False)):
        runtime = registry(tmp_path / str(days), monkeypatch, days=days)
        monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)
        await runtime.start()
        assert (runtime._retention_task is not None) is expected
        await runtime.shutdown()
        assert runtime._retention_task is None
