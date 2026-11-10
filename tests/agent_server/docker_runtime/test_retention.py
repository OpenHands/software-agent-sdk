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
    RuntimeArchivedError,
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
            conversation_storage_retention_days=days,
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
async def test_inactive_runtime_is_archived_and_history_kept(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=8)
    persisted = runtime.resolve_persisted_cipher(conversation_id).encrypt(
        SecretStr("llm-key")
    )

    await runtime.reclaimer.run_pass()

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
    assert list(runtime.reclaimer.trash.dir.iterdir()) == []


@pytest.mark.asyncio
async def test_recent_runtime_is_kept(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=6)

    await runtime.reclaimer.run_pass()

    assert (workspace / "main.py").is_file()
    assert runtime.runtime_info(conversation_id).can_resume is True


@pytest.mark.asyncio
async def test_live_runtime_is_kept_however_old(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, days_inactive=30)
    runtime._containers[conversation_id] = ConversationContainer(
        host="http://127.0.0.1", api_key="k", container_id="c"
    )

    await runtime.reclaimer.run_pass()

    assert (workspace / "main.py").is_file()
    assert not runtime.is_archived(conversation_id)


@pytest.mark.asyncio
async def test_caller_supplied_workspace_survives_archiving(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    checkout = tmp_path / "user-checkout"
    checkout.mkdir()
    conversation_id, workspace = provision(runtime, days_inactive=8, workspace=checkout)

    await runtime.reclaimer.run_pass()

    assert runtime.is_archived(conversation_id)
    assert (checkout / "main.py").is_file()


@pytest.mark.asyncio
async def test_archived_runtime_cannot_be_resumed(tmp_path, monkeypatch):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = provision(runtime, days_inactive=8)
    await runtime.reclaimer.run_pass()

    def no_docker(_conversation_id):
        pytest.fail("an archived runtime must not start a container")

    # Without this, a regression would start a real container and leak it.
    monkeypatch.setattr(runtime, "_build_container", no_docker)
    with pytest.raises(RuntimeArchivedError):
        await runtime.get_or_create(conversation_id)
    with pytest.raises(HTTPException) as raised:
        await routers._container(runtime, conversation_id)
    assert raised.value.status_code == 410


@pytest.mark.asyncio
async def test_interrupted_archiving_is_finished_by_the_next_pass(
    tmp_path, monkeypatch
):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = provision(runtime, days_inactive=8)
    # A crash after the marker, before the runtime dir was moved.
    runtime.archived_marker(conversation_id).touch()

    await runtime.reclaimer.run_pass()

    assert not runtime.provisioning.runtime_dir(conversation_id).exists()


@pytest.mark.asyncio
async def test_archiving_leaves_the_manifest_untouched(tmp_path, monkeypatch):
    # Older builds forbid unknown manifest fields; a rollback must still load it.
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, _ = provision(runtime, days_inactive=8)
    manifest = runtime.provisioning.manifest_path(conversation_id)
    before = manifest.read_bytes()

    await runtime.reclaimer.run_pass()

    assert runtime.is_archived(conversation_id)
    assert manifest.read_bytes() == before
    assert runtime.provisioning.load(conversation_id).conversation_id == conversation_id


def test_retention_days_is_read_from_nested_env(monkeypatch):
    from openhands.agent_server.config import load_config

    monkeypatch.setenv("OH_CONVERSATION_STORAGE_RETENTION_DAYS", "7")

    assert load_config().conversation_storage_retention_days == 7


@pytest.mark.asyncio
async def test_retention_loop_runs_only_when_configured(tmp_path, monkeypatch):
    for days, expected in ((7, True), (None, False)):
        runtime = registry(tmp_path / str(days), monkeypatch, days=days)
        monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)
        await runtime.start()
        assert (runtime.reclaimer._maintenance is not None) is expected
        await runtime.shutdown()
        assert runtime.reclaimer._maintenance is None
