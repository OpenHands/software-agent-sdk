"""Tests for has_any_secret: detect secret-bearing fields via the real
serialization pipeline, not a hardcoded field checklist.
"""

import logging
import tempfile
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.agent_server.persistence import (
    CustomSecret,
    FileSecretsStore,
    FileSettingsStore,
    PersistedSettings,
    Secrets,
)
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def persistence_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cipher():
    return Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))


def test_has_any_secret_detects_llm_api_key():
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"llm": {"model": "gpt-4o", "api_key": "sk-test"}}}
    )
    assert settings.has_any_secret


def test_has_any_secret_detects_critic_api_key():
    """critic_api_key is a separate secret from llm.api_key."""
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"verification": {"critic_api_key": "sk-critic"}}}
    )
    assert settings.has_any_secret


def test_has_any_secret_detects_mcp_server_secret():
    settings = PersistedSettings.model_validate(
        {
            "agent_settings": {
                "mcp_config": {
                    "mcpServers": {
                        "github": {
                            "command": "uvx",
                            "args": ["mcp-server-github"],
                            "env": {"GITHUB_TOKEN": "ghp-test"},
                        }
                    }
                }
            }
        }
    )
    assert settings.has_any_secret


def test_has_any_secret_detects_agent_context_secret():
    """agent_context.secrets values are plain str at rest (only secret-shaped
    at serialize time via their own field serializer), so a naive
    isinstance(v, SecretStr) walk would miss this -- must still be caught."""
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"agent_context": {"secrets": {"MY_TOKEN": "sk-ctx"}}}}
    )
    assert settings.has_any_secret


def test_has_any_secret_false_for_empty_settings():
    assert not PersistedSettings().has_any_secret


def test_secrets_has_any_secret_detects_custom_secret():
    secrets = Secrets(
        custom_secrets={
            "MY_SECRET": CustomSecret(name="MY_SECRET", secret=SecretStr("sk-test"))
        }
    )
    assert secrets.has_any_secret


def test_secrets_has_any_secret_false_for_empty_secrets():
    assert not Secrets().has_any_secret


def test_settings_save_warns_for_critic_key_only_without_cipher(
    persistence_dir, caplog
):
    """Regression test: before has_any_secret, the plaintext warning was
    gated on llm_api_key_is_set alone, so a critic_api_key-only settings
    object triggered no warning at all."""
    caplog.set_level(logging.WARNING)
    store = FileSettingsStore(persistence_dir=persistence_dir)
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"verification": {"critic_api_key": "sk-critic"}}}
    )

    store.save(settings)

    assert "PLAINTEXT" in caplog.text


def test_settings_save_no_warning_when_no_secrets_present(persistence_dir, caplog):
    caplog.set_level(logging.WARNING)
    store = FileSettingsStore(persistence_dir=persistence_dir)

    store.save(PersistedSettings())

    assert "PLAINTEXT" not in caplog.text


def test_settings_save_with_cipher_round_trips_critic_key(persistence_dir, cipher):
    store = FileSettingsStore(persistence_dir=persistence_dir, cipher=cipher)
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"verification": {"critic_api_key": "sk-critic"}}}
    )

    store.save(settings)

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.has_any_secret


def test_secrets_save_warns_without_cipher(persistence_dir, caplog):
    caplog.set_level(logging.WARNING)
    store = FileSecretsStore(persistence_dir=persistence_dir)
    secrets = Secrets(
        custom_secrets={
            "MY_SECRET": CustomSecret(name="MY_SECRET", secret=SecretStr("sk-test"))
        }
    )

    store.save(secrets)

    assert "PLAINTEXT" in caplog.text
