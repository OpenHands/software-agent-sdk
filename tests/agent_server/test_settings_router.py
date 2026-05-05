import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.persistence import reset_stores


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


# ── Tests for Settings/Secrets CRUD Endpoints ────────────────────────────


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
        reset_stores()


@pytest.fixture
def config_with_temp(temp_dir):
    """Create a config with temp directories."""
    return Config(
        session_api_keys=["test-key"],
        conversations_path=temp_dir / "conversations",
        bash_events_dir=temp_dir / "bash_events",
        secret_key=None,
        static_files_path=None,
        enable_vscode=False,
        preload_tools=False,
    )


@pytest.fixture
def auth_client(config_with_temp):
    """Create a test client with auth."""
    app = create_app(config_with_temp)
    return TestClient(app)


AUTH_HEADER = {"X-Session-API-Key": "test-key"}


class TestSettingsCRUD:
    """Tests for settings CRUD endpoints."""

    def test_get_settings_empty(self, auth_client):
        """GET /settings should return default settings."""
        response = auth_client.get("/api/settings", headers=AUTH_HEADER)
        assert response.status_code == 200

        data = response.json()
        assert "agent_settings" in data
        assert "conversation_settings" in data
        assert "llm_api_key_is_set" in data
        assert data["llm_api_key_is_set"] is False

    def test_update_settings(self, auth_client):
        """PATCH /settings should update settings."""
        response = auth_client.patch(
            "/api/settings",
            headers=AUTH_HEADER,
            json={"agent_settings_diff": {"llm": {"model": "gpt-4-turbo"}}},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["agent_settings"]["llm"]["model"] == "gpt-4-turbo"

    def test_update_settings_persists(self, config_with_temp):
        """Settings should persist across fresh app instances."""
        # First request: update settings
        app1 = create_app(config_with_temp)
        client1 = TestClient(app1)
        client1.patch(
            "/api/settings",
            headers=AUTH_HEADER,
            json={"agent_settings_diff": {"llm": {"model": "claude-3-opus"}}},
        )

        # Second request: verify persistence with fresh app instance
        reset_stores()  # Clear singleton cache
        app2 = create_app(config_with_temp)
        client2 = TestClient(app2)
        response = client2.get("/api/settings", headers=AUTH_HEADER)
        data = response.json()
        assert data["agent_settings"]["llm"]["model"] == "claude-3-opus"


class TestSecretsCRUD:
    """Tests for secrets CRUD endpoints."""

    def test_list_secrets_empty(self, auth_client):
        """GET /settings/secrets should return empty list initially."""
        response = auth_client.get("/api/settings/secrets", headers=AUTH_HEADER)
        assert response.status_code == 200
        assert response.json()["secrets"] == []

    def test_create_secret(self, auth_client):
        """PUT /settings/secrets should create a secret."""
        response = auth_client.put(
            "/api/settings/secrets",
            headers=AUTH_HEADER,
            json={"name": "MY_API_KEY", "value": "secret-value", "description": "Key"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "MY_API_KEY"

    def test_get_secret_value(self, auth_client):
        """GET /settings/secrets/{name} should return raw value."""
        auth_client.put(
            "/api/settings/secrets",
            headers=AUTH_HEADER,
            json={"name": "MY_SECRET", "value": "my-secret-value"},
        )

        response = auth_client.get(
            "/api/settings/secrets/MY_SECRET", headers=AUTH_HEADER
        )
        assert response.status_code == 200
        assert response.text == "my-secret-value"

    def test_get_secret_not_found(self, auth_client):
        """GET /settings/secrets/{name} should 404 for nonexistent."""
        response = auth_client.get(
            "/api/settings/secrets/NONEXISTENT", headers=AUTH_HEADER
        )
        assert response.status_code == 404

    def test_delete_secret(self, auth_client):
        """DELETE /settings/secrets/{name} should delete secret."""
        auth_client.put(
            "/api/settings/secrets",
            headers=AUTH_HEADER,
            json={"name": "TO_DELETE", "value": "value"},
        )

        response = auth_client.delete(
            "/api/settings/secrets/TO_DELETE", headers=AUTH_HEADER
        )
        assert response.status_code == 200

        response = auth_client.get(
            "/api/settings/secrets/TO_DELETE", headers=AUTH_HEADER
        )
        assert response.status_code == 404

    def test_invalid_secret_name(self, auth_client):
        """Secret names must follow naming rules."""
        # Name starting with number
        response = auth_client.put(
            "/api/settings/secrets",
            headers=AUTH_HEADER,
            json={"name": "123_INVALID", "value": "value"},
        )
        # 422 Unprocessable Entity - semantic validation error
        assert response.status_code == 422

        # Name with invalid characters
        response = auth_client.put(
            "/api/settings/secrets",
            headers=AUTH_HEADER,
            json={"name": "INVALID-NAME", "value": "value"},
        )
        assert response.status_code == 422
