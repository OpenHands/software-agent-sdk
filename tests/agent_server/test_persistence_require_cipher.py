"""Tests for require_cipher: refuse plaintext secret persistence when set."""

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
from openhands.sdk.utils.pydantic_secrets import MissingCipherError


@pytest.fixture
def persistence_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def cipher():
    return Cipher(urlsafe_b64encode(b"a" * 32).decode("ascii"))


def _settings_with_api_key() -> PersistedSettings:
    return PersistedSettings.model_validate(
        {"agent_settings": {"llm": {"model": "gpt-4o", "api_key": "sk-test-secret"}}}
    )


def _secrets_with_custom_secret() -> Secrets:
    return Secrets(
        custom_secrets={
            "MY_SECRET": CustomSecret(name="MY_SECRET", secret=SecretStr("sk-test"))
        }
    )


def test_settings_save_raises_without_cipher_when_require_cipher(persistence_dir):
    store = FileSettingsStore(persistence_dir=persistence_dir, require_cipher=True)

    with pytest.raises(MissingCipherError):
        store.save(_settings_with_api_key())


def test_settings_save_raises_for_critic_key_only_when_require_cipher(
    persistence_dir,
):
    """critic_api_key is a separate secret from llm.api_key -- must still guard it."""
    store = FileSettingsStore(persistence_dir=persistence_dir, require_cipher=True)
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"verification": {"critic_api_key": "sk-critic-test"}}}
    )

    with pytest.raises(MissingCipherError):
        store.save(settings)


def test_settings_save_raises_for_mcp_secret_when_require_cipher(persistence_dir):
    """MCP server env/header secrets must be guarded too, not just llm.api_key."""
    store = FileSettingsStore(persistence_dir=persistence_dir, require_cipher=True)
    settings = PersistedSettings.model_validate(
        {
            "agent_settings": {
                "mcp_config": {
                    "mcpServers": {
                        "github": {
                            "command": "uvx",
                            "args": ["mcp-server-github"],
                            "env": {"GITHUB_TOKEN": "ghp-test-secret"},
                        }
                    }
                }
            }
        }
    )

    with pytest.raises(MissingCipherError):
        store.save(settings)


def test_settings_save_raises_for_agent_context_secret_when_require_cipher(
    persistence_dir,
):
    """agent_context.secrets values are plain str at rest (only secret-shaped at
    serialize time), so a value-type walk alone would miss this -- must still
    be caught."""
    store = FileSettingsStore(persistence_dir=persistence_dir, require_cipher=True)
    settings = PersistedSettings.model_validate(
        {"agent_settings": {"agent_context": {"secrets": {"MY_TOKEN": "sk-ctx"}}}}
    )

    with pytest.raises(MissingCipherError):
        store.save(settings)


def test_secrets_save_raises_without_cipher_when_require_cipher(persistence_dir):
    store = FileSecretsStore(persistence_dir=persistence_dir, require_cipher=True)

    with pytest.raises(MissingCipherError):
        store.save(_secrets_with_custom_secret())


def test_settings_save_without_secrets_does_not_raise_when_require_cipher(
    persistence_dir,
):
    """No secrets present -> nothing to protect, no cipher needed."""
    store = FileSettingsStore(persistence_dir=persistence_dir, require_cipher=True)

    store.save(PersistedSettings())  # no api key set


def test_secrets_save_without_secrets_does_not_raise_when_require_cipher(
    persistence_dir,
):
    """No secrets present -> nothing to protect, no cipher needed."""
    store = FileSecretsStore(persistence_dir=persistence_dir, require_cipher=True)

    store.save(Secrets())  # empty custom_secrets


def test_settings_save_with_cipher_succeeds_when_require_cipher(
    persistence_dir, cipher
):
    store = FileSettingsStore(
        persistence_dir=persistence_dir, cipher=cipher, require_cipher=True
    )

    store.save(_settings_with_api_key())

    reloaded = store.load()
    assert reloaded is not None
    assert reloaded.llm_api_key_is_set


def test_secrets_save_with_cipher_succeeds_when_require_cipher(persistence_dir, cipher):
    store = FileSecretsStore(
        persistence_dir=persistence_dir, cipher=cipher, require_cipher=True
    )

    store.save(_secrets_with_custom_secret())

    reloaded = store.load()
    assert reloaded is not None
    assert "MY_SECRET" in reloaded.custom_secrets


def test_settings_save_without_cipher_stores_plaintext_by_default(persistence_dir):
    """require_cipher defaults to False -> unchanged backward-compatible behavior."""
    store = FileSettingsStore(persistence_dir=persistence_dir)

    store.save(_settings_with_api_key())  # does not raise

    raw = (persistence_dir / "settings.json").read_text()
    assert "sk-test-secret" in raw


def test_secrets_save_without_cipher_stores_plaintext_by_default(persistence_dir):
    """require_cipher defaults to False -> unchanged backward-compatible behavior."""
    store = FileSecretsStore(persistence_dir=persistence_dir)

    store.save(_secrets_with_custom_secret())  # does not raise

    raw = (persistence_dir / "secrets.json").read_text()
    assert "sk-test" in raw
