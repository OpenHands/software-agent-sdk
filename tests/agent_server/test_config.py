import json
import logging

import pytest
from pydantic import SecretStr, ValidationError

from openhands.agent_server.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONVERSATION_IDLE_TTL_SECONDS,
    V0_SESSION_API_KEY_ENV,
    V1_SESSION_API_KEY_ENV,
    Config,
    load_config,
)
from openhands.agent_server.init_router import InitRequest, _build_initialized_config


def test_load_config_reads_registered_marketplaces_from_env(monkeypatch, tmp_path):
    config_path = tmp_path / "missing.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv(
        "OH_REGISTERED_MARKETPLACES",
        json.dumps(
            [
                {
                    "name": "team",
                    "source": "https://github.com/org/marketplace",
                    "ref": "main",
                    "repo_path": "marketplace",
                    "auto_load": True,
                }
            ]
        ),
    )

    config = load_config()

    assert len(config.registered_marketplaces) == 1
    registration = config.registered_marketplaces[0]
    assert registration.name == "team"
    assert registration.source == "https://github.com/org/marketplace"
    assert registration.ref == "main"
    assert registration.repo_path == "marketplace"
    assert registration.auto_load is True


def test_load_config_reads_telemetry_deployment_kind_from_env(monkeypatch, tmp_path):
    config_path = tmp_path / "missing.json"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))
    monkeypatch.setenv("OH_TELEMETRY_DEPLOYMENT_KIND", "remote")

    assert load_config().telemetry.deployment_kind == "remote"


def test_conversation_idle_ttl_defaults_to_twenty_minutes():
    assert DEFAULT_CONVERSATION_IDLE_TTL_SECONDS == 1200.0
    assert Config().conversation_idle_ttl_seconds == 1200.0


def test_conversation_idle_ttl_can_be_disabled_and_overridden(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"conversation_idle_ttl_seconds": None}))
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))

    assert load_config().conversation_idle_ttl_seconds is None

    monkeypatch.setenv("OH_CONVERSATION_IDLE_TTL_SECONDS", "300")
    assert load_config().conversation_idle_ttl_seconds == 300.0


def test_conversation_idle_ttl_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        Config(conversation_idle_ttl_seconds=0)


def test_load_config_ignores_empty_session_api_key_slots(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "")

    assert load_config().session_api_keys == []

    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "sk-oh-real")
    monkeypatch.setenv("OH_SESSION_API_KEYS_1", "")

    assert load_config().session_api_keys == ["sk-oh-real"]


@pytest.fixture
def no_session_keys(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
    monkeypatch.delenv(V0_SESSION_API_KEY_ENV, raising=False)
    monkeypatch.delenv(V1_SESSION_API_KEY_ENV, raising=False)


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_empty_secret_key_env_is_treated_as_unset(
    monkeypatch, caplog, no_session_keys, blank
):
    monkeypatch.setenv("OH_SECRET_KEY", blank)

    config = load_config()

    assert config.secret_key is None
    with caplog.at_level(logging.WARNING, logger="openhands.agent_server.config"):
        assert config.cipher is None
    assert "OH_SECRET_KEY was not defined" in caplog.text


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_secret_key_env_falls_back_to_session_key(
    monkeypatch, no_session_keys, blank
):
    monkeypatch.setenv("OH_SECRET_KEY", blank)
    monkeypatch.setenv(V1_SESSION_API_KEY_ENV, "session-key")

    config = load_config()

    assert config.secret_key is not None
    assert config.secret_key.get_secret_value() == "session-key"
    assert config.cipher is not None
    assert config.cipher.secret_key == "session-key"


def test_blank_secret_key_in_config_file_is_treated_as_unset(
    monkeypatch, tmp_path, no_session_keys
):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"secret_key": "   "}))
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_file))
    monkeypatch.delenv("OH_SECRET_KEY", raising=False)

    config = load_config()

    assert config.secret_key is None
    assert config.cipher is None


def test_blank_secret_key_in_constructor_is_treated_as_unset(no_session_keys):
    assert Config(secret_key=SecretStr("  ")).secret_key is None
    secret_key = Config(secret_key=SecretStr("real")).secret_key
    assert secret_key is not None
    assert secret_key.get_secret_value() == "real"


def test_secret_key_env_with_surrounding_whitespace_is_kept_verbatim(
    monkeypatch, no_session_keys
):
    monkeypatch.setenv("OH_SECRET_KEY", " mykey ")

    cipher = load_config().cipher

    assert cipher is not None
    assert cipher.secret_key == " mykey "


def test_init_secret_key_with_surrounding_whitespace_is_kept_verbatim(
    no_session_keys,
):
    merged = _build_initialized_config(
        Config(deferred_init=True), InitRequest(secret_key=SecretStr(" mykey "))
    )

    assert merged.secret_key is not None
    assert merged.secret_key.get_secret_value() == " mykey "
