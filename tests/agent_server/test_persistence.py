"""Tests for the persistence module (settings and secrets storage)."""

import json
import tempfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from openhands.agent_server.persistence import (
    CustomSecret,
    PersistedSettings,
    Secrets,
    reset_stores,
)
from openhands.agent_server.persistence.store import (
    FileSecretsStore,
    FileSettingsStore,
)
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
        # Reset global stores after each test
        reset_stores()


@pytest.fixture
def cipher():
    """Create a cipher for encryption tests."""
    return Cipher("test-secret-key-1234567890")


class TestPersistedSettings:
    """Tests for PersistedSettings model."""

    def test_default_settings(self):
        """Default settings should have sensible defaults."""
        settings = PersistedSettings()
        assert settings.agent_settings is not None
        assert settings.conversation_settings is not None
        assert settings.llm_api_key_is_set is False

    def test_llm_api_key_is_set(self):
        """llm_api_key_is_set should reflect whether API key is configured."""
        settings = PersistedSettings()
        assert settings.llm_api_key_is_set is False

        # Set API key via update
        settings.update({"agent_settings_diff": {"llm": {"api_key": "test-api-key"}}})
        assert settings.llm_api_key_is_set is True

    def test_update_agent_settings(self):
        """update() should merge agent_settings_diff."""
        settings = PersistedSettings()

        settings.update({"agent_settings_diff": {"llm": {"model": "gpt-4-turbo"}}})
        assert settings.agent_settings.llm.model == "gpt-4-turbo"

    def test_update_conversation_settings(self):
        """update() should merge conversation_settings_diff."""
        settings = PersistedSettings()
        settings.update({"conversation_settings_diff": {"max_iterations": 50}})
        assert settings.conversation_settings.max_iterations == 50


class TestSecrets:
    """Tests for Secrets model."""

    def test_empty_secrets(self):
        """Empty secrets should have no custom_secrets."""
        secrets = Secrets()
        assert len(secrets.custom_secrets) == 0

    def test_add_secret(self):
        """Should be able to add custom secrets."""
        secrets = Secrets(
            custom_secrets={
                "API_KEY": CustomSecret(
                    name="API_KEY",
                    secret=SecretStr("secret-value"),
                    description="My API key",
                )
            }
        )
        assert "API_KEY" in secrets.custom_secrets
        assert (
            secrets.custom_secrets["API_KEY"].secret.get_secret_value()
            == "secret-value"
        )

    def test_get_env_vars(self):
        """get_env_vars() should return secret values as dict."""
        secrets = Secrets(
            custom_secrets={
                "KEY1": CustomSecret(
                    name="KEY1", secret=SecretStr("val1"), description=None
                ),
                "KEY2": CustomSecret(
                    name="KEY2", secret=SecretStr("val2"), description=None
                ),
            }
        )
        env_vars = secrets.get_env_vars()
        assert env_vars == {"KEY1": "val1", "KEY2": "val2"}

    def test_serialization_hides_secrets(self):
        """Serialization without expose_secrets should mask values."""
        secrets = Secrets(
            custom_secrets={
                "API_KEY": CustomSecret(
                    name="API_KEY",
                    secret=SecretStr("secret-value"),
                    description="desc",
                )
            }
        )
        data = secrets.model_dump(mode="json")
        assert data["custom_secrets"]["API_KEY"]["secret"] == "**********"

    def test_serialization_exposes_secrets(self):
        """Serialization with expose_secrets should show values."""
        secrets = Secrets(
            custom_secrets={
                "API_KEY": CustomSecret(
                    name="API_KEY",
                    secret=SecretStr("secret-value"),
                    description="desc",
                )
            }
        )
        data = secrets.model_dump(mode="json", context={"expose_secrets": True})
        assert data["custom_secrets"]["API_KEY"]["secret"] == "secret-value"


class TestFileSettingsStore:
    """Tests for FileSettingsStore."""

    def test_save_and_load(self, temp_dir):
        """Should save and load settings correctly."""
        store = FileSettingsStore(persistence_dir=temp_dir)

        settings = PersistedSettings()
        settings.update({"agent_settings_diff": {"llm": {"model": "claude-3-opus"}}})

        store.save(settings)
        loaded = store.load()

        assert loaded is not None
        assert loaded.agent_settings.llm.model == "claude-3-opus"

    def test_load_nonexistent(self, temp_dir):
        """Loading from nonexistent file should return None."""
        store = FileSettingsStore(persistence_dir=temp_dir)
        assert store.load() is None

    def test_encrypts_api_key(self, temp_dir, cipher):
        """Should encrypt API key when saving."""
        store = FileSettingsStore(persistence_dir=temp_dir, cipher=cipher)

        settings = PersistedSettings()
        settings.update(
            {"agent_settings_diff": {"llm": {"api_key": "my-secret-api-key"}}}
        )
        store.save(settings)

        # Read raw file and verify encryption
        raw_data = json.loads((temp_dir / "settings.json").read_text())
        api_key_stored = raw_data["agent_settings"]["llm"]["api_key"]

        # Encrypted value should not be the original
        assert api_key_stored != "my-secret-api-key"
        # Should be able to decrypt (cipher.decrypt returns SecretStr)
        decrypted = cipher.decrypt(api_key_stored)
        assert decrypted is not None
        assert decrypted.get_secret_value() == "my-secret-api-key"

    def test_decrypts_api_key_on_load(self, temp_dir, cipher):
        """Should decrypt API key when loading."""
        store = FileSettingsStore(persistence_dir=temp_dir, cipher=cipher)

        settings = PersistedSettings()
        settings.update(
            {"agent_settings_diff": {"llm": {"api_key": "my-secret-api-key"}}}
        )
        store.save(settings)

        loaded = store.load()
        assert loaded is not None
        api_key = loaded.agent_settings.llm.api_key
        assert api_key is not None
        # api_key is str | SecretStr - get the raw value
        api_key_value = (
            api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        )
        assert api_key_value == "my-secret-api-key"


class TestFileSecretsStore:
    """Tests for FileSecretsStore."""

    def test_save_and_load(self, temp_dir):
        """Should save and load secrets correctly."""
        store = FileSecretsStore(persistence_dir=temp_dir)

        secrets = Secrets(
            custom_secrets={
                "MY_SECRET": CustomSecret(
                    name="MY_SECRET",
                    secret=SecretStr("secret-value"),
                    description="A secret",
                )
            }
        )
        store.save(secrets)
        loaded = store.load()

        assert loaded is not None
        assert "MY_SECRET" in loaded.custom_secrets
        assert (
            loaded.custom_secrets["MY_SECRET"].secret.get_secret_value()
            == "secret-value"
        )

    def test_load_nonexistent(self, temp_dir):
        """Loading from nonexistent file should return None."""
        store = FileSecretsStore(persistence_dir=temp_dir)
        assert store.load() is None

    def test_get_secret(self, temp_dir):
        """get_secret() should return single secret value."""
        store = FileSecretsStore(persistence_dir=temp_dir)
        store.set_secret("KEY1", "value1", "Description 1")

        assert store.get_secret("KEY1") == "value1"
        assert store.get_secret("NONEXISTENT") is None

    def test_set_secret(self, temp_dir):
        """set_secret() should create or update a secret."""
        store = FileSecretsStore(persistence_dir=temp_dir)

        store.set_secret("KEY1", "value1", "Initial")
        assert store.get_secret("KEY1") == "value1"

        # Update
        store.set_secret("KEY1", "updated", "Updated")
        assert store.get_secret("KEY1") == "updated"

        loaded = store.load()
        assert loaded is not None
        assert loaded.custom_secrets["KEY1"].description == "Updated"

    def test_delete_secret(self, temp_dir):
        """delete_secret() should remove a secret."""
        store = FileSecretsStore(persistence_dir=temp_dir)
        store.set_secret("KEY1", "value1")
        store.set_secret("KEY2", "value2")

        assert store.delete_secret("KEY1") is True
        assert store.get_secret("KEY1") is None
        assert store.get_secret("KEY2") == "value2"

        # Deleting nonexistent should return False
        assert store.delete_secret("NONEXISTENT") is False

    def test_encrypts_secrets(self, temp_dir, cipher):
        """Should encrypt secrets when saving."""
        store = FileSecretsStore(persistence_dir=temp_dir, cipher=cipher)
        store.set_secret("API_KEY", "super-secret-value")

        # Read raw file and verify encryption
        raw_data = json.loads((temp_dir / "secrets.json").read_text())
        stored_secret = raw_data["custom_secrets"]["API_KEY"]["secret"]

        assert stored_secret != "super-secret-value"
        # cipher.decrypt returns SecretStr
        decrypted = cipher.decrypt(stored_secret)
        assert decrypted is not None
        assert decrypted.get_secret_value() == "super-secret-value"

    def test_decrypts_secrets_on_load(self, temp_dir, cipher):
        """Should decrypt secrets when loading."""
        store = FileSecretsStore(persistence_dir=temp_dir, cipher=cipher)
        store.set_secret("API_KEY", "super-secret-value")

        # Load and verify decryption
        loaded = store.load()
        assert loaded is not None
        assert (
            loaded.custom_secrets["API_KEY"].secret.get_secret_value()
            == "super-secret-value"
        )
