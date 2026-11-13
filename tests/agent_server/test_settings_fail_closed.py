"""Fail-closed reads of a present-but-unreadable ``settings.json`` (#6370).

A corrupt, schema-invalid, or differently-encrypted settings file used to
load as ``None`` and silently substitute ``PersistedSettings()`` defaults —
whose ``confirmation_mode=False`` removed the operator's approval boundary.
The conversation-building read paths must instead answer with the same 409
integrity error ``update()`` and the profile-activation endpoints raise,
while an absent file keeps the first-run defaults.
"""

import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.openai.service import _create_conversation_request
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_agent_profile_store,
    get_llm_profile_store,
    get_settings_store,
    reset_stores,
)
from openhands.agent_server.persistence.store import FileSettingsStore
from openhands.sdk import LLM
from openhands.sdk.llm.meta_profile_store import MetaProfile
from openhands.sdk.profiles import OpenHandsAgentProfile
from openhands.sdk.settings.model import OpenHandsAgentSettings
from openhands.sdk.utils.cipher import Cipher


INTEGRITY_DETAIL = "Settings file is corrupted or encrypted with a different key"
# The exact bytes from the #6370 report: a settings.json truncated mid-write.
CORRUPT_BYTES = b'{"agent_settings": '


def _apply_llm_api_key(s: PersistedSettings, key: str) -> PersistedSettings:
    s.update({"agent_settings_diff": {"llm": {"api_key": key}}})
    return s


# ── Store level ──────────────────────────────────────────────────────────────


def test_load_fail_closed_returns_none_when_the_file_is_absent(tmp_path):
    store = FileSettingsStore(persistence_dir=tmp_path)
    assert store.load_fail_closed() is None


@pytest.mark.parametrize(
    "corrupt_bytes",
    [
        pytest.param(CORRUPT_BYTES, id="truncated-json"),
        pytest.param(b"null", id="json-null"),
        pytest.param(
            b'{"conversation_settings": {"confirmation_mode": {"deep": "object"}}}',
            id="schema-invalid",
        ),
    ],
)
def test_load_fail_closed_raises_when_the_file_is_present_but_unreadable(
    tmp_path, corrupt_bytes
):
    store = FileSettingsStore(tmp_path)
    (tmp_path / "settings.json").write_bytes(corrupt_bytes)
    with pytest.raises(RuntimeError, match="different key"):
        store.load_fail_closed()


def test_load_fail_closed_raises_on_secrets_encrypted_under_a_different_key(tmp_path):
    writer = FileSettingsStore(tmp_path, cipher=Cipher("qa-cipher-key-a"))
    writer.update(lambda s: _apply_llm_api_key(s, "sk-seeded-under-key-a"))
    reader = FileSettingsStore(tmp_path, cipher=Cipher("qa-cipher-key-b"))
    # The non-strict load stays lenient (secrets become None) — only the
    # fail-closed read must refuse to serve anything.
    assert reader.load() is not None
    with pytest.raises(RuntimeError, match="different key"):
        reader.load_fail_closed()


def test_load_fail_closed_returns_settings_for_a_valid_file(tmp_path):
    cipher = Cipher("qa-cipher-key")
    store = FileSettingsStore(tmp_path, cipher=cipher)
    store.update(lambda s: _apply_llm_api_key(s, "sk-live"))
    settings = store.load_fail_closed()
    assert settings is not None
    agent_settings = settings.agent_settings
    assert isinstance(agent_settings, OpenHandsAgentSettings)
    assert isinstance(agent_settings.llm, LLM)
    assert agent_settings.llm.api_key is not None
    assert isinstance(agent_settings.llm.api_key, SecretStr)
    assert agent_settings.llm.api_key.get_secret_value() == "sk-live"


def test_load_fail_closed_log_names_the_file_without_secrets(tmp_path, caplog):
    store = FileSettingsStore(persistence_dir=tmp_path)
    path = tmp_path / "settings.json"
    path.write_bytes(CORRUPT_BYTES)
    with pytest.raises(RuntimeError, match="settings.json") as exc_info:
        store.load_fail_closed()
    # The operator-facing error names the file (so it can be repaired) but
    # never echoes file content or decrypted secrets.
    assert "sk-" not in str(exc_info.value)
    assert "sk-" not in caplog.text


# ── Router level ─────────────────────────────────────────────────────────────


@pytest.fixture
def temp_settings_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        settings_dir = Path(tmpdir) / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        yield settings_dir


@pytest.fixture
def app_config(temp_settings_dir, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(temp_settings_dir))
    return Config(static_files_path=None, session_api_keys=[], secret_key=None)


@pytest.fixture
def client(app_config):
    reset_stores()
    app = create_app(app_config)
    yield TestClient(app)
    reset_stores()


@pytest.fixture
def settings_path(temp_settings_dir) -> Path:
    return temp_settings_dir / "settings.json"


def _seed_active_profile(app_config: Config) -> OpenHandsAgentProfile:
    get_llm_profile_store().save("fast", LLM(model="fast-model"))
    profile = OpenHandsAgentProfile(name="oh", llm_profile_ref="fast")
    get_agent_profile_store().save(profile)
    get_settings_store(app_config).save(
        PersistedSettings(active_agent_profile_id=str(profile.id))
    )
    return profile


def test_get_settings_answers_409_on_corrupt_file_and_recovers(client, settings_path):
    seed = client.patch(
        "/api/settings",
        json={"conversation_settings_diff": {"confirmation_mode": True}},
    )
    assert seed.status_code == 200
    good_bytes = settings_path.read_bytes()

    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.get("/api/settings")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL
    # The response must not leak the server's file layout.
    assert str(settings_path) not in resp.text

    # Repairing the file restores the endpoint with the real settings.
    settings_path.write_bytes(good_bytes)
    repaired = client.get("/api/settings")
    assert repaired.status_code == 200
    assert repaired.json()["conversation_settings"]["confirmation_mode"] is True


def test_get_settings_serves_defaults_while_the_file_is_absent(client, settings_path):
    assert not settings_path.exists()
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["conversation_settings"]["confirmation_mode"] is False


def test_agent_profiles_list_answers_409_on_corrupt_file(client, settings_path):
    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.get("/api/agent-profiles")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL


def test_agent_profiles_list_serves_defaults_while_the_file_is_absent(
    client, settings_path
):
    assert not settings_path.exists()
    resp = client.get("/api/agent-profiles")
    assert resp.status_code == 200


def test_agent_profile_delete_answers_409_on_corrupt_file(
    app_config, client, settings_path
):
    profile = _seed_active_profile(app_config)
    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.delete(f"/api/agent-profiles/{profile.name}")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL


def test_agent_profile_materialize_answers_409_on_corrupt_file(
    app_config, client, settings_path
):
    _seed_active_profile(app_config)
    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.post("/api/agent-profiles/oh/materialize")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL


def test_meta_profiles_list_answers_409_on_corrupt_file(client, settings_path):
    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.get("/api/meta-profiles")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL


def test_meta_profile_delete_answers_409_on_corrupt_file(client, settings_path):
    from openhands.agent_server.meta_profiles_router import _get_meta_profile_store

    _get_meta_profile_store().save(
        "r",
        MetaProfile.model_validate(
            {
                "classifier_model": "minimax",
                "classes": [{"description": "UI", "model": "deepseek"}],
            }
        ),
    )
    settings_path.write_bytes(CORRUPT_BYTES)
    resp = client.delete("/api/meta-profiles/r")
    assert resp.status_code == 409
    assert resp.json()["detail"] == INTEGRITY_DETAIL


def test_gateway_conversation_request_answers_409_on_corrupt_file(
    app_config, settings_path, monkeypatch
):
    monkeypatch.setattr(
        "openhands.agent_server.launch.discover_profile_skills", lambda: []
    )
    _seed_active_profile(app_config)
    settings_path.write_bytes(CORRUPT_BYTES)
    with pytest.raises(HTTPException) as exc_info:
        _create_conversation_request(
            model="openhands_fast",
            system_text="Answer briefly.",
            user_content=[],
            config=app_config,
            conversation_id=None,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == INTEGRITY_DETAIL
