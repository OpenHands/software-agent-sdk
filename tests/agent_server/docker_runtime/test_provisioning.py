from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore


def test_runtime_control_identity_is_private_and_survives_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-encryption-key"),
        session_api_keys=["outer-api-key"],
    )
    store = RuntimeProvisioningStore(config)
    assert store.data_root.stat().st_mode & 0o777 == 0o700
    first_id, second_id = uuid4(), uuid4()
    first = store.create(first_id)
    second = store.create(second_id)
    assert first.api_key != second.api_key
    assert first.api_key.get_secret_value() != "outer-api-key"
    assert first.encryption_key != second.encryption_key
    assert first.encryption_key != config.secret_key
    reopened = RuntimeProvisioningStore(config).load(first_id)
    assert reopened == first
    manifest = store.manifest_path(first_id)
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert manifest.parent.stat().st_mode & 0o777 == 0o700
    assert first.api_key.get_secret_value() not in manifest.read_text()
    assert first.encryption_key.get_secret_value() not in manifest.read_text()
    assert not manifest.is_relative_to(store.runtime_dir(first_id))
    assert not manifest.is_relative_to(config.conversations_path)
    assert not manifest.is_relative_to(config.workspace_path)


def test_legacy_runtime_is_rejected_without_modifying_persisted_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(
        conversations_path=tmp_path / "conversations",
        secret_key=SecretStr("outer"),
    )
    store = RuntimeProvisioningStore(config)
    cid = uuid4()
    directory = config.conversations_path / cid.hex
    directory.mkdir(parents=True)
    state = directory / "base_state.json"
    state.write_text("legacy state")
    with pytest.raises(ValueError, match="legacy"):
        store.create(cid)
    assert state.read_text() == "legacy state"


@pytest.mark.parametrize("source", ["/", "global", "conversations", "workspaces"])
def test_runtime_rejects_mounts_exposing_control_or_other_conversations(
    tmp_path, source, monkeypatch
):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    path = Path("/") if source == "/" else tmp_path / source
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer"),
        conversation_container_volumes=[f"{path}:/exposed"],
    )
    with pytest.raises(ValueError, match="mount"):
        RuntimeProvisioningStore(config)


def test_default_workspace_mount_source_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    config = Config(secret_key=SecretStr("outer"))
    store = RuntimeProvisioningStore(config)
    cid = uuid4()
    workspace = store.workspace_dir(cid)
    assert workspace.is_absolute()
    assert workspace == config.workspace_path.resolve() / cid.hex
    assert store.conversation_dir(cid) == config.conversations_path.resolve() / cid.hex


@pytest.mark.parametrize("symlink_root", [False, True])
def test_workspace_mount_rejects_symlinks(tmp_path, monkeypatch, symlink_root):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    root = tmp_path / "workspaces"
    target = tmp_path / "outside"
    target.mkdir()
    cid = uuid4()
    if symlink_root:
        root.symlink_to(target, target_is_directory=True)
    else:
        root.mkdir()
        (root / cid.hex).symlink_to(target, target_is_directory=True)
    store = RuntimeProvisioningStore(
        Config(workspace_path=root, secret_key=SecretStr("outer"))
    )
    with pytest.raises(ValueError, match="symlink"):
        store.workspace_dir(cid)
