from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore


def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspaces",
        secret_key=SecretStr("outer-key"),
        session_api_keys=["outer-session"],
    )


def test_each_runtime_gets_encrypted_independent_credentials(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    store = RuntimeProvisioningStore(runtime_config)
    first = store.create(uuid4())
    second = store.create(uuid4())

    assert first.api_key != second.api_key
    assert first.encryption_key != second.encryption_key
    assert first.api_key.get_secret_value() != "outer-session"
    assert RuntimeProvisioningStore(runtime_config).load(first.conversation_id) == first
    manifest = store.manifest_path(first.conversation_id)
    serialized = manifest.read_text()
    assert first.api_key.get_secret_value() not in serialized
    assert first.encryption_key.get_secret_value() not in serialized
    assert manifest.parent.stat().st_mode & 0o777 == 0o700


def test_runtime_identity_cache_refreshes_after_manifest_update(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    writer = RuntimeProvisioningStore(runtime_config)
    identity = writer.create(uuid4())
    reader = RuntimeProvisioningStore(runtime_config)

    cached = reader.load(identity.conversation_id)
    assert reader.load(identity.conversation_id) is cached

    updated = identity.model_copy(update={"api_key": SecretStr("rotated-key")})
    writer.save(updated)
    refreshed = reader.load(identity.conversation_id)

    assert refreshed is not cached
    assert refreshed.api_key.get_secret_value() == "rotated-key"


def test_optional_load_distinguishes_missing_from_invalid_identity(
    tmp_path, monkeypatch
):
    store = RuntimeProvisioningStore(config(tmp_path, monkeypatch))
    conversation_id = uuid4()

    assert store.load_optional(conversation_id) is None

    store.manifest_path(conversation_id).write_text("not-json")
    with pytest.raises(ValueError):
        store.load_optional(conversation_id)


def test_existing_local_conversation_is_not_reinterpreted(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    store = RuntimeProvisioningStore(runtime_config)
    conversation_id = uuid4()
    directory = runtime_config.conversations_path / conversation_id.hex
    directory.mkdir(parents=True)
    (directory / "base_state.json").write_text("local state")

    with pytest.raises(ValueError, match="existing local conversation"):
        store.create(conversation_id)


def test_runtime_keeps_the_selected_workspace(tmp_path, monkeypatch):
    store = RuntimeProvisioningStore(config(tmp_path, monkeypatch))
    conversation_id = uuid4()
    workspace = tmp_path / "automation-run"
    other = tmp_path / "other-run"
    workspace.mkdir()
    other.mkdir()

    assert store.create(conversation_id, workspace).workspace_path == workspace
    assert store.create(conversation_id, workspace).workspace_path == workspace
    with pytest.raises(ValueError, match="cannot change workspaces"):
        store.create(conversation_id, other)


def test_runtime_creates_a_missing_selected_workspace(tmp_path, monkeypatch):
    store = RuntimeProvisioningStore(config(tmp_path, monkeypatch))
    workspace = tmp_path / "new" / "conversation-workspace"

    identity = store.create(uuid4(), workspace)

    assert identity.workspace_path == workspace
    assert workspace.is_dir()


def test_runtime_mount_rejects_symlink(tmp_path, monkeypatch):
    runtime_config = config(tmp_path, monkeypatch)
    runtime_config.workspace_path.mkdir()
    conversation_id = uuid4()
    target = tmp_path / "outside"
    target.mkdir()
    (runtime_config.workspace_path / conversation_id.hex).symlink_to(
        target, target_is_directory=True
    )

    store = RuntimeProvisioningStore(runtime_config)
    with pytest.raises(ValueError, match="symlink"):
        store.direct_child(runtime_config.workspace_path, conversation_id.hex)
