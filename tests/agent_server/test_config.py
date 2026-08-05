import json

import pytest
from pydantic import ValidationError

from openhands.agent_server.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONVERSATION_IDLE_TTL_SECONDS,
    Config,
    load_config,
)


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


def test_cipher_recomputed_when_secret_key_changes_across_model_copy():
    """Regression (deferred-init stale cipher): ``Config.cipher`` caches the
    built ``Cipher`` on the instance, and ``model_copy`` duplicates that cache.
    A copy carrying a *new* secret_key must not keep encrypting under the old
    one — that is exactly how /api/init's delivered secret_key was ignored."""
    from pydantic import SecretStr

    from openhands.sdk.utils.cipher import Cipher

    base = Config(secret_key=SecretStr("old-key"))
    assert base.cipher is not None  # prime the instance cache

    copied = Config.model_copy(base, update={"secret_key": SecretStr("new-key")})
    cipher = copied.cipher
    assert cipher is not None
    token = cipher.encrypt(SecretStr("payload"))
    assert token is not None

    decrypted = Cipher("new-key").decrypt(token)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "payload"
    # And the original config still uses its own key.
    assert Cipher("old-key").decrypt(base.cipher.encrypt(SecretStr("x"))) is not None
