"""Regression tests for #6370: unreadable settings must not become defaults.

``FileSettingsStore.load()`` reports every unreadable state as ``None``, and
``load() or PersistedSettings()`` turned that into ``confirmation_mode=False``
and therefore ``NeverConfirm``. The reads that build conversations now use
``load_existing()`` and answer 409, while ``load()`` keeps its lenient contract.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.openai.service import _create_conversation_request
from openhands.agent_server.persistence import (
    FileSettingsStore,
    PersistedSettings,
    SettingsFileUnreadableError,
    get_llm_profile_store,
    reset_stores,
)
from openhands.agent_server.persistence.models import SettingsUpdatePayload
from openhands.sdk import LLM
from openhands.sdk.utils.cipher import Cipher


KEY_A = "qa-fail-closed-key-a"
KEY_B = "qa-fail-closed-key-b"
SECRET = "sk-fail-closed-secret"

TRUNCATED = '{"schema_version": 6, "conversation_settings": '
SCHEMA_INVALID = '{"schema_version": 999}'


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """Point the settings store at ``tmp_path`` and hand back that directory."""
    path = tmp_path / ".openhands"
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(path))
    reset_stores()
    yield path
    reset_stores()


@pytest.fixture
def client(settings_dir):
    app = create_app(
        Config(static_files_path=None, session_api_keys=[], secret_key=None)
    )
    return TestClient(app)


def _write(settings_dir: Path, raw: str) -> Path:
    path = settings_dir / "settings.json"
    path.write_text(raw, encoding="utf-8")
    return path


def _apply(
    settings: PersistedSettings, payload: SettingsUpdatePayload
) -> PersistedSettings:
    settings.update(payload)
    return settings


def _seed_secret(settings_dir: Path, key: str) -> None:
    FileSettingsStore(settings_dir, cipher=Cipher(key)).update(
        lambda s: _apply(s, {"agent_settings_diff": {"llm": {"api_key": SECRET}}})
    )


# -- FileSettingsStore.load_existing ------------------------------------------


def test_load_existing_returns_none_when_the_file_is_absent(settings_dir):
    assert FileSettingsStore(settings_dir).load_existing() is None


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (TRUNCATED, "parse"),
        ("null", "validation"),
        (SCHEMA_INVALID, "validation"),
    ],
    ids=["truncated-json", "json-null", "newer-schema"],
)
def test_load_existing_raises_for_unreadable_payloads(settings_dir, raw, reason):
    path = _write(settings_dir, raw)
    store = FileSettingsStore(settings_dir)

    with pytest.raises(SettingsFileUnreadableError) as excinfo:
        store.load_existing()

    assert excinfo.value.reason == reason
    assert excinfo.value.path == path
    # The lenient contract is unchanged, so existing callers keep working.
    assert store.load() is None


def test_load_existing_refuses_secrets_under_another_key(settings_dir):
    _seed_secret(settings_dir, KEY_A)
    reader = FileSettingsStore(settings_dir, cipher=Cipher(KEY_B))

    with pytest.raises(SettingsFileUnreadableError) as excinfo:
        reader.load_existing()

    assert excinfo.value.reason == "decryption"
    assert reader.load() is not None


def test_update_still_refuses_to_overwrite_an_unreadable_file(settings_dir):
    _write(settings_dir, TRUNCATED)

    with pytest.raises(RuntimeError, match="different key"):
        FileSettingsStore(settings_dir).update(lambda s: s)

    assert (settings_dir / "settings.json").read_text(encoding="utf-8") == TRUNCATED


# -- read paths that build conversations --------------------------------------


@pytest.mark.parametrize(
    "raw",
    [TRUNCATED, "null", SCHEMA_INVALID],
    ids=["truncated-json", "json-null", "newer-schema"],
)
def test_get_settings_returns_409_for_an_unreadable_file(client, settings_dir, raw):
    _write(settings_dir, raw)

    response = client.get("/api/settings")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Settings file is corrupted or encrypted with a different key"
    }
    assert str(settings_dir) not in response.text


def test_get_settings_returns_defaults_without_a_file(client):
    response = client.get("/api/settings")

    assert response.status_code == 200
    assert response.json()["conversation_settings"]["confirmation_mode"] is False


@pytest.mark.parametrize("route", ["/api/agent-profiles", "/api/meta-profiles"])
def test_profile_listing_fails_closed(client, settings_dir, route):
    _write(settings_dir, TRUNCATED)

    response = client.get(route)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Settings file is corrupted or encrypted with a different key"
    }


def test_unreadable_settings_log_names_the_file_and_reason(settings_dir, caplog):
    path = _write(settings_dir, TRUNCATED)

    with caplog.at_level("ERROR", logger="openhands.agent_server.persistence.store"):
        with pytest.raises(SettingsFileUnreadableError):
            FileSettingsStore(settings_dir).load_existing()

    assert str(path) in caplog.text
    assert "parse" in caplog.text


def test_a_secret_is_not_echoed_back_with_the_integrity_error(client, settings_dir):
    _seed_secret(settings_dir, KEY_A)
    path = settings_dir / "settings.json"
    path.write_text(TRUNCATED + SECRET, encoding="utf-8")

    response = client.get("/api/settings")

    assert response.status_code == 409
    assert SECRET not in response.text
    assert path.name not in response.text


def test_the_gateway_refuses_an_unreadable_file(settings_dir, tmp_path):
    """The gateway's conversation build raises instead of using defaults."""
    config = Config(
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        session_api_keys=[],
    )
    get_llm_profile_store().save("fast", LLM(model="fast-model"))
    _write(settings_dir, TRUNCATED)

    with pytest.raises(SettingsFileUnreadableError):
        _create_conversation_request(
            model="openhands_fast",
            system_text="",
            user_content=[],
            config=config,
            conversation_id=None,
        )
