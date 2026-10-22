import json
import logging
import os

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


def test_env_is_none_overrides_config_file_value(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app_backend_public_url": "http://file.invalid:9",
                "conversation_idle_ttl_seconds": 600,
            }
        )
    )
    monkeypatch.setenv(CONFIG_PATH_ENV, str(config_path))

    # Without an environment value the file value is kept.
    assert load_config().app_backend_public_url == "http://file.invalid:9"

    monkeypatch.setenv("OH_APP_BACKEND_PUBLIC_URL", "http://env.invalid:9")
    assert load_config().app_backend_public_url == "http://env.invalid:9"
    monkeypatch.delenv("OH_APP_BACKEND_PUBLIC_URL")

    monkeypatch.setenv("OH_APP_BACKEND_PUBLIC_URL_IS_NONE", "1")
    monkeypatch.setenv("OH_CONVERSATION_IDLE_TTL_SECONDS_IS_NONE", "1")
    config = load_config()
    assert config.app_backend_public_url is None
    assert config.conversation_idle_ttl_seconds is None


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


@pytest.mark.parametrize(
    "env",
    [
        {"OH_PREVIOUS_SECRET_KEYS": '["old-1", "old-2"]'},
        {"OH_PREVIOUS_SECRET_KEYS_0": "old-1", "OH_PREVIOUS_SECRET_KEYS_1": "old-2"},
    ],
    ids=["json-list", "indexed"],
)
def test_load_config_reads_previous_secret_keys(monkeypatch, tmp_path, env):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setenv("OH_SECRET_KEY", "new")
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    config = load_config()
    assert [k.get_secret_value() for k in config.previous_secret_keys] == [
        "old-1",
        "old-2",
    ]
    assert config.cipher is not None
    assert config.cipher.secret_key == "new"
    assert config.cipher.previous_keys == ("old-1", "old-2")


def test_rotated_session_key_fallback_reads_with_previous_key(monkeypatch, tmp_path):
    """Without OH_SECRET_KEY the cipher key is the first session API key, so a
    rotated session key needs the old one in OH_PREVIOUS_SECRET_KEYS."""
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
    monkeypatch.delenv("OH_SECRET_KEY", raising=False)
    monkeypatch.delenv(V0_SESSION_API_KEY_ENV, raising=False)
    monkeypatch.setenv(V1_SESSION_API_KEY_ENV, "old-session-key")
    old_cipher = load_config().cipher
    assert old_cipher is not None
    token = old_cipher.encrypt(SecretStr("value"))
    assert token is not None

    monkeypatch.setenv(V1_SESSION_API_KEY_ENV, "new-session-key")
    rotated_without_old = load_config().cipher
    assert rotated_without_old is not None
    assert rotated_without_old.try_decrypt_str(token) is None

    monkeypatch.setenv("OH_PREVIOUS_SECRET_KEYS_0", "old-session-key")
    cipher = load_config().cipher
    assert cipher is not None
    assert cipher.secret_key == "new-session-key"
    assert cipher.try_decrypt_str(token) == "value"


def test_cipher_follows_keys_changed_by_model_copy():
    base = Config(secret_key=SecretStr("a"), session_api_keys=[])
    assert base.cipher is not None
    copy = base.model_copy(
        update={
            "secret_key": SecretStr("b"),
            "previous_secret_keys": [SecretStr("a")],
        }
    )
    assert copy.cipher is not None
    assert copy.cipher.secret_key == "b"
    assert copy.cipher.previous_keys == ("a",)


class TestExportSessionApiKey:
    def test_exports_first_key(self, monkeypatch):
        from openhands.agent_server.config import (
            V1_SESSION_API_KEY_ENV,
            Config,
            export_session_api_key,
        )

        monkeypatch.delenv(V1_SESSION_API_KEY_ENV, raising=False)

        export_session_api_key(Config(session_api_keys=["first", "second"]))

        assert os.environ[V1_SESSION_API_KEY_ENV] == "first"

    def test_replaces_stale_key(self, monkeypatch):
        from openhands.agent_server.config import (
            V1_SESSION_API_KEY_ENV,
            Config,
            export_session_api_key,
        )

        monkeypatch.setenv(V1_SESSION_API_KEY_ENV, "stale")

        export_session_api_key(Config(session_api_keys=["fresh"]))

        assert os.environ[V1_SESSION_API_KEY_ENV] == "fresh"

    def test_no_keys_leaves_env_untouched(self, monkeypatch):
        from openhands.agent_server.config import (
            V0_SESSION_API_KEY_ENV,
            V1_SESSION_API_KEY_ENV,
            Config,
            export_session_api_key,
        )

        monkeypatch.delenv(V0_SESSION_API_KEY_ENV, raising=False)
        monkeypatch.delenv(V1_SESSION_API_KEY_ENV, raising=False)

        export_session_api_key(Config(session_api_keys=[]))

        assert V1_SESSION_API_KEY_ENV not in os.environ
