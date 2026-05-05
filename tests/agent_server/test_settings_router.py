import os
import tempfile
from base64 import urlsafe_b64encode
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import (
    FileSettingsStore,
    PersistedSettings,
    reset_stores,
)
from openhands.sdk.utils.cipher import Cipher


@pytest.fixture
def temp_persistence_dir():
    """Create a temporary directory for persistence files and reset stores."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Reset global store singletons before test
        reset_stores()
        # Set environment variable for persistence directory
        old_val = os.environ.get("OH_PERSISTENCE_DIR")
        os.environ["OH_PERSISTENCE_DIR"] = tmpdir
        yield Path(tmpdir)
        # Cleanup: reset stores and restore environment
        reset_stores()
        if old_val is not None:
            os.environ["OH_PERSISTENCE_DIR"] = old_val
        else:
            os.environ.pop("OH_PERSISTENCE_DIR", None)


@pytest.fixture
def secret_key():
    """Generate a valid Fernet key."""
    return urlsafe_b64encode(b"a" * 32).decode("ascii")


@pytest.fixture
def config_with_settings(temp_persistence_dir, secret_key):
    """Create a config with secret key for encryption."""
    return Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr(secret_key),
    )


@pytest.fixture
def client_with_settings(config_with_settings):
    """Create a test client with settings support."""
    return TestClient(create_app(config_with_settings))


def test_get_agent_settings_schema():
    client = TestClient(create_app(Config(static_files_path=None, session_api_keys=[])))

    response = client.get("/api/settings/agent-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "AgentSettings"

    section_keys = [section["key"] for section in body["sections"]]
    assert "llm" in section_keys
    assert "condenser" in section_keys
    assert "verification" in section_keys

    verification_section = next(
        section for section in body["sections"] if section["key"] == "verification"
    )
    verification_field_keys = {field["key"] for field in verification_section["fields"]}
    assert "verification.critic_enabled" in verification_field_keys
    assert "confirmation_mode" not in verification_field_keys
    assert "security_analyzer" not in verification_field_keys


def test_get_conversation_settings_schema():
    client = TestClient(create_app(Config(static_files_path=None, session_api_keys=[])))

    response = client.get("/api/settings/conversation-schema")

    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "ConversationSettings"

    section_keys = [section["key"] for section in body["sections"]]
    assert section_keys == ["general", "verification"]

    verification_section = next(
        section for section in body["sections"] if section["key"] == "verification"
    )
    verification_field_keys = {field["key"] for field in verification_section["fields"]}
    assert "confirmation_mode" in verification_field_keys
    assert "security_analyzer" in verification_field_keys


# ── GET /api/settings tests ─────────────────────────────────────────────


def test_get_settings_returns_default_settings(client_with_settings):
    """GET /api/settings returns default settings when none are persisted."""
    response = client_with_settings.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert "agent_settings" in body
    assert "conversation_settings" in body
    assert "llm_api_key_is_set" in body
    assert body["llm_api_key_is_set"] is False


def test_get_settings_without_header_redacts_secrets(
    client_with_settings, temp_persistence_dir, secret_key
):
    """GET /api/settings without X-Expose-Secrets header redacts secrets."""
    # First, save settings with a secret using the store
    cipher = Cipher(secret_key)
    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-secret-key")
    store.save(settings)

    response = client_with_settings.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    # Secret should be redacted (Pydantic default behavior)
    api_key = body["agent_settings"]["llm"]["api_key"]
    assert api_key == "**********"
    assert body["llm_api_key_is_set"] is True


def test_get_settings_with_plaintext_header_exposes_secrets(
    client_with_settings, temp_persistence_dir, secret_key
):
    """GET /api/settings with X-Expose-Secrets: plaintext returns raw secrets."""
    # Save settings with a secret
    cipher = Cipher(secret_key)
    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-secret-key")
    store.save(settings)

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )

    assert response.status_code == 200
    body = response.json()
    # Secret should be exposed
    api_key = body["agent_settings"]["llm"]["api_key"]
    assert api_key == "sk-test-secret-key"


def test_get_settings_with_encrypted_header_encrypts_secrets(
    client_with_settings, temp_persistence_dir, secret_key
):
    """GET /api/settings with X-Expose-Secrets: encrypted returns encrypted secrets."""
    # Save settings with a secret
    cipher = Cipher(secret_key)
    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-secret-key")
    store.save(settings)

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "encrypted"}
    )

    assert response.status_code == 200
    body = response.json()
    api_key = body["agent_settings"]["llm"]["api_key"]
    # Should be encrypted (not plaintext, not redacted)
    assert api_key != "sk-test-secret-key"
    assert api_key != "**********"
    # Should be decryptable
    decrypted = cipher.decrypt(api_key)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret-key"


def test_get_settings_with_true_header_treats_as_encrypted(
    client_with_settings, temp_persistence_dir, secret_key
):
    """GET /api/settings with X-Expose-Secrets: true treats as encrypted (safety)."""
    # Save settings with a secret
    cipher = Cipher(secret_key)
    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-secret-key")
    store.save(settings)

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "true"}
    )

    assert response.status_code == 200
    body = response.json()
    api_key = body["agent_settings"]["llm"]["api_key"]
    # Should be encrypted (not plaintext)
    assert api_key != "sk-test-secret-key"
    # Should be decryptable
    decrypted = cipher.decrypt(api_key)
    assert decrypted is not None
    assert decrypted.get_secret_value() == "sk-test-secret-key"


def test_get_settings_with_invalid_header_returns_400(client_with_settings):
    """GET /api/settings with invalid X-Expose-Secrets value returns 400."""
    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "invalid-value"}
    )

    assert response.status_code == 400
    assert "Invalid X-Expose-Secrets header" in response.json()["detail"]


# ── PATCH /api/settings tests ───────────────────────────────────────────


def test_patch_settings_updates_llm_config(client_with_settings):
    """PATCH /api/settings can update LLM configuration."""
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {"llm": {"model": "gpt-4o", "api_key": "sk-new-key"}}
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["llm"]["model"] == "gpt-4o"
    # Response should NOT expose secrets (no header)
    assert body["agent_settings"]["llm"]["api_key"] == "**********"
    assert body["llm_api_key_is_set"] is True


def test_patch_settings_empty_payload_returns_400(client_with_settings):
    """PATCH /api/settings with empty payload returns 400."""
    response = client_with_settings.patch("/api/settings", json={})

    assert response.status_code == 400
    assert "At least one of" in response.json()["detail"]


def test_patch_settings_deep_merges(client_with_settings):
    """PATCH /api/settings deep-merges with existing settings."""
    # First update: set model
    client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )

    # Second update: set api_key (should preserve model)
    response = client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"api_key": "sk-test-key"}}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["llm"]["model"] == "gpt-4o"
    assert body["llm_api_key_is_set"] is True


# ── Secrets CRUD tests ──────────────────────────────────────────────────


def test_list_secrets_empty(client_with_settings):
    """GET /api/settings/secrets returns empty list when no secrets exist."""
    response = client_with_settings.get("/api/settings/secrets")

    assert response.status_code == 200
    body = response.json()
    assert body["secrets"] == []


def test_create_and_list_secrets(client_with_settings):
    """PUT /api/settings/secrets creates a secret, GET lists it."""
    # Create a secret
    create_response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "secret-value", "description": "Test"},
    )

    assert create_response.status_code == 200
    assert create_response.json()["name"] == "MY_SECRET"
    assert create_response.json()["description"] == "Test"

    # List secrets (should NOT include value)
    list_response = client_with_settings.get("/api/settings/secrets")

    assert list_response.status_code == 200
    secrets = list_response.json()["secrets"]
    assert len(secrets) == 1
    assert secrets[0]["name"] == "MY_SECRET"
    assert secrets[0]["description"] == "Test"
    assert "value" not in secrets[0]


def test_get_secret_value(client_with_settings):
    """GET /api/settings/secrets/{name} returns the raw secret value."""
    # Create a secret
    client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "secret-value-123"},
    )

    # Get the secret value
    response = client_with_settings.get("/api/settings/secrets/MY_SECRET")

    assert response.status_code == 200
    assert response.text == "secret-value-123"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_get_secret_value_not_found(client_with_settings):
    """GET /api/settings/secrets/{name} returns 404 for nonexistent secret."""
    response = client_with_settings.get("/api/settings/secrets/NONEXISTENT")

    assert response.status_code == 404


def test_delete_secret(client_with_settings):
    """DELETE /api/settings/secrets/{name} deletes the secret."""
    # Create a secret
    client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "secret-value"},
    )

    # Delete it
    delete_response = client_with_settings.delete("/api/settings/secrets/MY_SECRET")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True

    # Verify it's gone
    get_response = client_with_settings.get("/api/settings/secrets/MY_SECRET")
    assert get_response.status_code == 404


def test_secret_name_validation(client_with_settings):
    """PUT /api/settings/secrets validates secret name format."""
    # Invalid: starts with number
    response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "123_invalid", "value": "test"},
    )
    assert response.status_code == 422

    # Invalid: contains special characters
    response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "invalid-name", "value": "test"},
    )
    assert response.status_code == 422

    # Valid: starts with letter, alphanumeric + underscore
    response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "VALID_NAME_123", "value": "test"},
    )
    assert response.status_code == 200


# ── PATCH validation and error handling tests ───────────────────────────


def test_patch_settings_validation_error_returns_422(client_with_settings):
    """PATCH /api/settings with invalid data returns 422."""
    # Invalid: negative max_iterations
    response = client_with_settings.patch(
        "/api/settings",
        json={"conversation_settings_diff": {"max_iterations": -5}},
    )
    assert response.status_code == 422
    # Error message should be sanitized (not expose secrets)
    assert response.json()["detail"] == "Settings validation failed"


def test_patch_settings_validation_error_does_not_leak_secrets(client_with_settings):
    """PATCH validation errors don't leak secret values in error messages."""
    # Try to update with invalid model value (causes validation to fail)
    # This tests that even if the API key was in memory during validation,
    # it doesn't appear in error messages
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {
                    "api_key": "sk-secret-value",
                    "model": "",
                }  # Empty model is invalid
            }
        },
    )
    # Should return 422 with sanitized message
    assert response.status_code == 422
    # The error message should be sanitized - NOT contain the secret value
    error_detail = response.json()["detail"]
    assert "sk-secret-value" not in error_detail
    # And it should be the generic sanitized message
    assert error_detail == "Settings validation failed"


def test_secret_upsert_updates_existing(client_with_settings):
    """PUT /api/settings/secrets updates existing secret (upsert behavior)."""
    # Create initial secret
    client_with_settings.put(
        "/api/settings/secrets",
        json={
            "name": "MY_SECRET",
            "value": "original-value",
            "description": "Original",
        },
    )

    # Update the secret (same name, new value)
    update_response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "updated-value", "description": "Updated"},
    )
    assert update_response.status_code == 200
    assert update_response.json()["description"] == "Updated"

    # Verify the value was updated
    get_response = client_with_settings.get("/api/settings/secrets/MY_SECRET")
    assert get_response.status_code == 200
    assert get_response.text == "updated-value"


def test_secret_name_validation_on_get(client_with_settings):
    """GET /api/settings/secrets/{name} validates name format."""
    # Invalid name format
    response = client_with_settings.get("/api/settings/secrets/123_invalid")
    assert response.status_code == 422


def test_secret_name_validation_on_delete(client_with_settings):
    """DELETE /api/settings/secrets/{name} validates name format."""
    # Invalid name format
    response = client_with_settings.delete("/api/settings/secrets/invalid-name")
    assert response.status_code == 422


# ── Concurrent update tests ────────────────────────────────────────────────


def test_concurrent_patch_updates_preserve_data(client_with_settings):
    """PATCH /api/settings handles concurrent updates without data loss.

    Tests that multiple sequential PATCH requests don't corrupt settings
    or lose updates due to race conditions in the file locking mechanism.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Initialize settings
    client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "initial-model"}}},
    )

    results = []
    errors = []

    def update_settings(model_name: str):
        """Make a PATCH request to update the model."""
        try:
            response = client_with_settings.patch(
                "/api/settings",
                json={"agent_settings_diff": {"llm": {"model": model_name}}},
            )
            return (model_name, response.status_code)
        except Exception as e:
            return (model_name, str(e))

    # Run concurrent updates
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(update_settings, f"model-{i}") for i in range(10)]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            if result[1] != 200:
                errors.append(result)

    # All requests should succeed (file locking should serialize them)
    assert len(errors) == 0, f"Some requests failed: {errors}"

    # Final state should be consistent (one of the model values)
    final_response = client_with_settings.get("/api/settings")
    assert final_response.status_code == 200
    final_model = final_response.json()["agent_settings"]["llm"]["model"]
    # The final value should be one of the values we set (not corrupted)
    assert final_model.startswith("model-"), f"Unexpected model value: {final_model}"


# ── Error handling tests ───────────────────────────────────────────────────


def test_get_settings_encrypted_mode_without_cipher_returns_503(temp_persistence_dir):
    """GET /api/settings with X-Expose-Secrets: encrypted without cipher returns 503.

    When OH_SECRET_KEY is not set, config.cipher is None and requesting
    encrypted mode should fail fast with a clear error (503 Service Unavailable).
    """
    # Create a config WITHOUT secret_key (cipher will be None)
    config = Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=None,  # No cipher!
    )
    client = TestClient(create_app(config))

    # First, verify we can create settings (no cipher needed for plaintext)
    # Note: Without cipher, we need to manually create a settings file
    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=None)
    settings = PersistedSettings()
    settings.agent_settings.llm.api_key = SecretStr("sk-test-secret-key")
    store.save(settings)

    # Now request encrypted mode - should fail because no cipher
    response = client.get("/api/settings", headers={"X-Expose-Secrets": "encrypted"})

    # Should return 503 (service unavailable - encryption not configured)
    assert response.status_code == 503
    body = response.json()
    # Error message may be in 'detail' or 'exception' depending on error handler config
    error_text = body.get("detail", "") + body.get("exception", "")
    assert "OH_SECRET_KEY" in error_text


def test_patch_settings_corrupted_file_returns_409(
    client_with_settings, temp_persistence_dir
):
    """PATCH /api/settings returns 409 when settings file is corrupted.

    Tests the RuntimeError handling path that catches corruption or
    encryption key mismatches.
    """
    # Initialize valid settings first
    client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4"}}},
    )

    # Corrupt the settings file directly
    settings_file = temp_persistence_dir / "settings.json"
    settings_file.write_text("{ this is not valid JSON !!!}")

    # Attempt to update - should fail with 409 (corruption detected)
    response = client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )

    # RuntimeError from store.update() should be caught and returned as 409
    assert response.status_code == 409
    assert "corrupted" in response.json()["detail"].lower()


# ── Corrupted secrets file tests ───────────────────────────────────────────


def test_create_secret_corrupted_file_returns_500(
    client_with_settings, temp_persistence_dir
):
    """PUT /api/settings/secrets returns 500 when secrets file is corrupted.

    Tests that the data loss protection path is triggered when set_secret()
    encounters a corrupted secrets file.
    """
    # Create initial secret
    client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "test"},
    )

    # Corrupt the secrets file
    secrets_file = temp_persistence_dir / "secrets.json"
    secrets_file.write_text("{ corrupted !!!}")

    # Attempt to create new secret - should fail to prevent data loss
    response = client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "OTHER_SECRET", "value": "value"},
    )

    assert response.status_code == 500


def test_delete_secret_corrupted_file_returns_500(
    client_with_settings, temp_persistence_dir
):
    """DELETE /api/settings/secrets returns 500 when secrets file is corrupted.

    Tests that the data loss protection path is triggered when delete_secret()
    encounters a corrupted secrets file.
    """
    # Create initial secret
    client_with_settings.put(
        "/api/settings/secrets",
        json={"name": "MY_SECRET", "value": "test"},
    )

    # Corrupt the secrets file
    secrets_file = temp_persistence_dir / "secrets.json"
    secrets_file.write_text("{ corrupted !!!}")

    # Attempt to delete secret - should fail to prevent data loss
    response = client_with_settings.delete("/api/settings/secrets/MY_SECRET")

    assert response.status_code == 500
