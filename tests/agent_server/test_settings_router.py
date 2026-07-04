import json
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
    PERSISTED_SETTINGS_SCHEMA_VERSION,
    FileSettingsStore,
    PersistedSettings,
    reset_stores,
)
from openhands.agent_server.persistence.models import _deep_merge
from openhands.sdk.llm import LLM
from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    CONVERSATION_SETTINGS_SCHEMA_VERSION,
    ACPAgentSettings,
    OpenHandsAgentSettings,
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


def _encrypt(cipher: Cipher, value: str) -> str:
    encrypted = cipher.encrypt(SecretStr(value))
    assert encrypted is not None
    return encrypted


def _write_settings_file(persistence_dir: Path, payload: dict) -> None:
    (persistence_dir / "settings.json").write_text(json.dumps(payload, indent=2))


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
    assert body["active_profile"] is None


def test_get_settings_migrates_legacy_openhands_settings_and_resaves_current(
    client_with_settings, temp_persistence_dir, secret_key
):
    """Old OpenHands settings files load, migrate, and remain editable."""
    cipher = Cipher(secret_key)
    _write_settings_file(
        temp_persistence_dir,
        {
            "active_profile": "legacy-profile",
            "agent_settings": {
                "schema_version": 1,
                "agent_kind": "llm",
                "llm": {
                    "model": "legacy-model",
                    "api_key": _encrypt(cipher, "sk-legacy-agent-key"),
                },
                "tools": [{"name": "TerminalTool"}],
                "enable_sub_agents": False,
                "enable_switch_llm_tool": True,
                "mcp_config": {
                    "mcpServers": {
                        "github": {
                            "command": "uvx",
                            "args": ["mcp-server-github"],
                            "env": {
                                "GITHUB_TOKEN": _encrypt(cipher, "ghp-legacy-mcp-token")
                            },
                        },
                        "remote": {
                            "url": "https://example.com/mcp",
                            "headers": {
                                "Authorization": _encrypt(
                                    cipher, "Bearer legacy-mcp-token"
                                )
                            },
                        },
                    }
                },
                "condenser": {"enabled": False, "max_size": 120},
                "verification": {
                    "critic_enabled": True,
                    "confirmation_mode": True,
                    "security_analyzer": "llm",
                },
            },
            "conversation_settings": {
                "max_iterations": 42,
                "confirmation_mode": True,
                "security_analyzer": "llm",
            },
        },
    )

    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    loaded = store.load()

    assert loaded is not None
    assert loaded.active_profile == "legacy-profile"
    assert loaded.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION

    assert loaded.agent_settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert isinstance(loaded.agent_settings, OpenHandsAgentSettings)

    assert loaded.agent_settings.agent_kind == "openhands"
    assert loaded.agent_settings.llm.model == "legacy-model"
    assert isinstance(loaded.agent_settings.llm.api_key, SecretStr)
    assert loaded.agent_settings.llm.api_key.get_secret_value() == "sk-legacy-agent-key"
    assert loaded.conversation_settings.schema_version == (
        CONVERSATION_SETTINGS_SCHEMA_VERSION
    )
    assert loaded.conversation_settings.max_iterations == 42
    assert loaded.conversation_settings.confirmation_mode is True
    assert loaded.conversation_settings.security_analyzer == "llm"

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active_profile"] == "legacy-profile"
    agent_settings = body["agent_settings"]
    assert agent_settings["schema_version"] == AGENT_SETTINGS_SCHEMA_VERSION
    assert agent_settings["agent_kind"] == "openhands"
    assert agent_settings["llm"]["api_key"] == "sk-legacy-agent-key"
    assert agent_settings["condenser"] == {
        "enabled": False,
        "condenser_kind": "llm_summarizing",
        "max_size": 120,
        "max_tokens": None,
        "keep_first": 2,
        "minimum_progress": 0.1,
        "hard_context_reset_max_retries": 5,
        "hard_context_reset_context_scaling": 0.8,
    }
    assert agent_settings["verification"]["critic_enabled"] is True
    assert "confirmation_mode" not in agent_settings["verification"]
    assert "security_analyzer" not in agent_settings["verification"]
    servers = agent_settings["mcp_config"]["mcpServers"]
    assert servers["github"]["env"]["GITHUB_TOKEN"] == "ghp-legacy-mcp-token"
    assert servers["remote"]["auth"] == {
        "strategy": "header",
        "headers": {"Authorization": "Bearer legacy-mcp-token"},
    }
    assert "headers" not in servers["remote"]
    assert body["conversation_settings"] == {
        "schema_version": CONVERSATION_SETTINGS_SCHEMA_VERSION,
        "max_iterations": 42,
        "confirmation_mode": True,
        "security_analyzer": "llm",
    }

    patch_response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {"llm": {"model": "post-migration-model"}},
            "conversation_settings_diff": {"max_iterations": 84},
        },
    )
    assert patch_response.status_code == 200, patch_response.text

    on_disk_text = (temp_persistence_dir / "settings.json").read_text()
    assert "sk-legacy-agent-key" not in on_disk_text
    assert "ghp-legacy-mcp-token" not in on_disk_text
    assert "Bearer legacy-mcp-token" not in on_disk_text

    on_disk = json.loads(on_disk_text)
    assert on_disk["schema_version"] == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert on_disk["active_profile"] == "legacy-profile"
    assert on_disk["agent_settings"]["schema_version"] == AGENT_SETTINGS_SCHEMA_VERSION
    assert on_disk["agent_settings"]["agent_kind"] == "openhands"
    assert on_disk["conversation_settings"]["max_iterations"] == 84

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["llm"]["model"] == "post-migration-model"
    assert body["agent_settings"]["llm"]["api_key"] == "sk-legacy-agent-key"
    servers = body["agent_settings"]["mcp_config"]["mcpServers"]
    assert servers["github"]["env"]["GITHUB_TOKEN"] == "ghp-legacy-mcp-token"
    assert servers["remote"]["auth"] == {
        "strategy": "header",
        "headers": {"Authorization": "Bearer legacy-mcp-token"},
    }
    assert "headers" not in servers["remote"]
    assert body["conversation_settings"]["max_iterations"] == 84


def test_get_settings_migrates_acp_settings_and_resaves_encrypted_credentials(
    client_with_settings, temp_persistence_dir, secret_key
):
    """ACP settings use the same persisted migration/encryption path."""
    cipher = Cipher(secret_key)
    _write_settings_file(
        temp_persistence_dir,
        {
            "agent_settings": {
                "schema_version": 1,
                "agent_kind": "acp",
                "acp_server": "custom",
                "acp_command": ["echo", "settings"],
                "acp_args": ["--verbose"],
                "acp_model": "acp-test-model",
                "acp_session_mode": "bypassPermissions",
                "acp_prompt_timeout": 123.0,
                "llm": {
                    "model": "acp-attribution-model",
                    "api_key": _encrypt(cipher, "sk-acp-llm"),
                },
            },
            "conversation_settings": {"max_iterations": 77},
        },
    )

    store = FileSettingsStore(persistence_dir=temp_persistence_dir, cipher=cipher)
    loaded = store.load()

    assert loaded is not None
    assert loaded.schema_version == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert loaded.agent_settings.schema_version == AGENT_SETTINGS_SCHEMA_VERSION
    assert isinstance(loaded.agent_settings, ACPAgentSettings)

    assert loaded.agent_settings.agent_kind == "acp"
    assert loaded.agent_settings.acp_command == ["echo", "settings"]
    assert loaded.agent_settings.acp_args == ["--verbose"]
    assert loaded.agent_settings.acp_model == "acp-test-model"
    assert loaded.agent_settings.acp_session_mode == "bypassPermissions"
    assert loaded.agent_settings.acp_prompt_timeout == 123.0
    assert isinstance(loaded.agent_settings.llm.api_key, SecretStr)
    assert loaded.agent_settings.llm.api_key.get_secret_value() == "sk-acp-llm"

    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert response.status_code == 200
    agent_settings = response.json()["agent_settings"]
    assert agent_settings["schema_version"] == AGENT_SETTINGS_SCHEMA_VERSION
    assert agent_settings["agent_kind"] == "acp"
    assert agent_settings["llm"]["api_key"] == "sk-acp-llm"

    patch_response = client_with_settings.patch(
        "/api/settings", json={"conversation_settings_diff": {"max_iterations": 88}}
    )
    assert patch_response.status_code == 200, patch_response.text

    on_disk_text = (temp_persistence_dir / "settings.json").read_text()
    assert "sk-acp-llm" not in on_disk_text
    on_disk = json.loads(on_disk_text)
    assert on_disk["schema_version"] == PERSISTED_SETTINGS_SCHEMA_VERSION
    assert on_disk["agent_settings"]["llm"]["api_key"].startswith("gAAAA")
    assert on_disk["conversation_settings"]["max_iterations"] == 88

    reloaded = store.load()
    assert reloaded is not None
    assert isinstance(reloaded.agent_settings, ACPAgentSettings)

    assert reloaded.conversation_settings.max_iterations == 88


def test_persisted_settings_from_persisted_rejects_newer_schema_version() -> None:
    with pytest.raises(ValueError, match="newer than supported"):
        PersistedSettings.from_persisted(
            {"schema_version": PERSISTED_SETTINGS_SCHEMA_VERSION + 1}
        )


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


def test_mcp_oauth_state_is_encrypted_and_round_trips(
    client_with_settings, temp_persistence_dir, secret_key
):
    """OAuth tokens stored on MCP server objects use settings secret handling."""
    patch_response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "mcp_config": {
                    "mcpServers": {
                        "superhuman": {
                            "url": "https://mcp.example.com/mcp",
                            "auth": {
                                "strategy": "oauth2",
                                "state": {
                                    "tokens": {
                                        "access_token": "oauth-access-token",
                                        "refresh_token": "oauth-refresh-token",
                                    },
                                    "token_expires_at": 12345.0,
                                },
                            },
                        }
                    }
                }
            }
        },
    )

    assert patch_response.status_code == 200, patch_response.text
    redacted_state = patch_response.json()["agent_settings"]["mcp_config"][
        "mcpServers"
    ]["superhuman"]["auth"]["state"]
    assert redacted_state["tokens"]["access_token"] == "**********"

    on_disk_text = (temp_persistence_dir / "settings.json").read_text()
    assert "oauth-access-token" not in on_disk_text
    assert "oauth-refresh-token" not in on_disk_text
    on_disk = json.loads(on_disk_text)
    encrypted_value = on_disk["agent_settings"]["mcp_config"]["mcpServers"][
        "superhuman"
    ]["auth"]["state"]["tokens"]
    assert encrypted_value["access_token"].startswith("gAAAA")
    assert encrypted_value["refresh_token"].startswith("gAAAA")

    plaintext_response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert plaintext_response.status_code == 200
    plaintext_value = plaintext_response.json()["agent_settings"]["mcp_config"][
        "mcpServers"
    ]["superhuman"]["auth"]["state"]
    assert plaintext_value == {
        "tokens": {
            "access_token": "oauth-access-token",
            "refresh_token": "oauth-refresh-token",
        },
        "token_expires_at": 12345.0,
    }

    encrypted_response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "encrypted"}
    )
    assert encrypted_response.status_code == 200
    encrypted_round_trip_value = encrypted_response.json()["agent_settings"][
        "mcp_config"
    ]["mcpServers"]["superhuman"]["auth"]["state"]["tokens"]
    assert encrypted_round_trip_value["access_token"].startswith("gAAAA")


def test_patch_settings_decrypts_encrypted_mcp_oauth_state(
    client_with_settings, temp_persistence_dir, secret_key
):
    """Encrypted OAuth state from a frontend round-trip is not double-saved."""
    cipher = Cipher(secret_key)
    patch_response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "mcp_config": {
                    "mcpServers": {
                        "superhuman": {
                            "url": "https://mcp.example.com/mcp",
                            "auth": {
                                "strategy": "oauth2",
                                "state": {
                                    "tokens": {
                                        "access_token": _encrypt(
                                            cipher, "oauth-access-token"
                                        ),
                                        "refresh_token": _encrypt(
                                            cipher, "oauth-refresh-token"
                                        ),
                                        "token_type": "Bearer",
                                    },
                                },
                            },
                        }
                    }
                }
            }
        },
    )

    assert patch_response.status_code == 200, patch_response.text

    plaintext_response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert plaintext_response.status_code == 200
    plaintext_value = plaintext_response.json()["agent_settings"]["mcp_config"][
        "mcpServers"
    ]["superhuman"]["auth"]["state"]["tokens"]
    assert plaintext_value == {
        "access_token": "oauth-access-token",
        "refresh_token": "oauth-refresh-token",
    }

    on_disk_text = (temp_persistence_dir / "settings.json").read_text()
    assert "oauth-access-token" not in on_disk_text
    assert "oauth-refresh-token" not in on_disk_text


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


def test_patch_settings_updates_active_profile(client_with_settings):
    """PATCH /api/settings can update and clear the active LLM profile."""
    response = client_with_settings.patch(
        "/api/settings",
        json={"active_profile": "fast-profile"},
    )

    assert response.status_code == 200
    assert response.json()["active_profile"] == "fast-profile"

    refetch = client_with_settings.get("/api/settings")
    assert refetch.status_code == 200
    assert refetch.json()["active_profile"] == "fast-profile"

    clear_response = client_with_settings.patch(
        "/api/settings",
        json={"active_profile": None},
    )

    assert clear_response.status_code == 200
    assert clear_response.json()["active_profile"] is None

    refetch = client_with_settings.get("/api/settings")
    assert refetch.status_code == 200
    assert refetch.json()["active_profile"] is None


def test_patch_settings_rejects_invalid_active_profile(client_with_settings):
    """PATCH /api/settings validates active profile names."""
    response = client_with_settings.patch(
        "/api/settings",
        json={"active_profile": "not a valid profile"},
    )

    assert response.status_code == 422


def test_patch_settings_active_agent_profile_id_independent(client_with_settings):
    """active_agent_profile_id sets/clears independently of active_profile."""
    agent_id = "12345678-1234-1234-1234-1234567890ab"
    set_response = client_with_settings.patch(
        "/api/settings",
        json={"active_profile": "fast-profile", "active_agent_profile_id": agent_id},
    )
    assert set_response.status_code == 200
    body = set_response.json()
    assert body["active_profile"] == "fast-profile"
    assert body["active_agent_profile_id"] == agent_id

    # Clearing the agent pointer must leave the LLM profile pointer untouched.
    clear_response = client_with_settings.patch(
        "/api/settings",
        json={"active_agent_profile_id": None},
    )
    assert clear_response.status_code == 200
    cleared = clear_response.json()
    assert cleared["active_agent_profile_id"] is None
    assert cleared["active_profile"] == "fast-profile"

    refetch = client_with_settings.get("/api/settings").json()
    assert refetch["active_agent_profile_id"] is None
    assert refetch["active_profile"] == "fast-profile"


def test_patch_settings_rejects_malformed_active_agent_profile_id(client_with_settings):
    """A non-UUID active_agent_profile_id is rejected at the HTTP layer."""
    response = client_with_settings.patch(
        "/api/settings",
        json={"active_agent_profile_id": "not-a-uuid"},
    )
    assert response.status_code == 422


def test_existing_settings_load_with_null_active_agent_profile_id(
    temp_persistence_dir, config_with_settings
):
    """A settings file predating the field loads with active_agent_profile_id=None."""
    _write_settings_file(
        temp_persistence_dir,
        {
            "schema_version": PERSISTED_SETTINGS_SCHEMA_VERSION,
            "agent_settings": {"agent_kind": "openhands"},
            "active_profile": "legacy-profile",
        },
    )

    client = TestClient(create_app(config_with_settings))
    response = client.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["active_profile"] == "legacy-profile"
    assert body["active_agent_profile_id"] is None


def test_patch_settings_updates_condenser_config(client_with_settings):
    """PATCH /api/settings can update condenser constructor settings."""
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "condenser": {
                    "enabled": True,
                    "condenser_kind": "llm_summarizing",
                    "max_size": 120,
                    "max_tokens": 56000,
                    "keep_first": 3,
                    "minimum_progress": 0.2,
                    "hard_context_reset_max_retries": 7,
                    "hard_context_reset_context_scaling": 0.6,
                }
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["agent_settings"]["condenser"] == {
        "enabled": True,
        "condenser_kind": "llm_summarizing",
        "max_size": 120,
        "max_tokens": 56000,
        "keep_first": 3,
        "minimum_progress": 0.2,
        "hard_context_reset_max_retries": 7,
        "hard_context_reset_context_scaling": 0.6,
    }


def test_patch_settings_switches_condenser_variant(client_with_settings):
    """PATCH /api/settings can switch to a different condenser settings variant."""
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "condenser": {
                    "enabled": True,
                    "condenser_kind": "no_op",
                }
            }
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["agent_settings"]["condenser"] == {
        "enabled": True,
        "condenser_kind": "no_op",
    }


def test_patch_settings_encrypts_mcp_env_and_headers_on_disk(
    client_with_settings, temp_persistence_dir
):
    """PATCH /api/settings must encrypt MCP ``env`` / ``headers`` values at
    rest with the configured cipher — the same way other secret fields are
    persisted — and never write redacted sentinels or plaintext.

    Reading them back via ``X-Expose-Secrets: plaintext`` must round-trip
    to the original values (decrypted on load).
    """
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "mcp_config": {
                    "mcpServers": {
                        "github": {
                            "command": "uvx",
                            "args": ["mcp-server-github"],
                            "env": {"GITHUB_TOKEN": "ghp-router-secret"},
                        },
                        "remote": {
                            "url": "https://example.com/mcp",
                            "headers": {"Authorization": "Bearer tok-router-secret"},
                        },
                    }
                }
            }
        },
    )
    assert response.status_code == 200, response.text

    # Inspect the on-disk settings.json: plaintext must NOT appear, the
    # values must be Fernet ciphertext.
    on_disk_path = temp_persistence_dir / "settings.json"
    on_disk_text = on_disk_path.read_text()
    assert "<redacted>" not in on_disk_text
    assert "**********" not in on_disk_text
    assert "ghp-router-secret" not in on_disk_text
    assert "tok-router-secret" not in on_disk_text

    on_disk = json.loads(on_disk_text)
    servers_on_disk = on_disk["agent_settings"]["mcp_config"]["mcpServers"]
    assert servers_on_disk["github"]["env"]["GITHUB_TOKEN"].startswith("gAAAA")
    assert servers_on_disk["remote"]["headers"]["Authorization"].startswith("gAAAA")
    # Non-secret structure must remain readable.
    assert servers_on_disk["github"]["command"] == "uvx"
    assert servers_on_disk["remote"]["url"] == "https://example.com/mcp"

    # GET with plaintext decrypts and returns the original round-tripped values.
    response = client_with_settings.get(
        "/api/settings", headers={"X-Expose-Secrets": "plaintext"}
    )
    assert response.status_code == 200
    servers = response.json()["agent_settings"]["mcp_config"]["mcpServers"]
    assert servers["github"]["env"]["GITHUB_TOKEN"] == "ghp-router-secret"
    assert servers["remote"]["headers"] == {
        "Authorization": "Bearer tok-router-secret",
    }


def test_patch_settings_empty_payload_returns_400(client_with_settings):
    """PATCH /api/settings with empty payload returns 400."""
    response = client_with_settings.patch("/api/settings", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "At least one of agent_settings_diff, "
        "conversation_settings_diff, misc_settings_diff, "
        "active_profile, or active_agent_profile_id must be provided"
    )


# ── misc_settings (opaque frontend-owned container) ─────────────────────
#
# These tests exercise the persistence + deep-merge behaviour of the
# ``misc_settings`` container. The agent-server treats it as opaque, so the
# payloads below use neutral keys/values whose only purpose is to exercise
# the merge machinery — they intentionally do not reference any specific
# frontend's schema.


def test_get_settings_returns_empty_misc_settings_by_default(client_with_settings):
    """GET /api/settings returns an empty misc_settings dict by default."""
    response = client_with_settings.get("/api/settings")

    assert response.status_code == 200
    body = response.json()
    assert "misc_settings" in body
    assert body["misc_settings"] == {}


def test_patch_settings_writes_misc_settings(client_with_settings):
    """PATCH /api/settings with misc_settings_diff persists the payload."""
    payload = {
        "theme": "dark",
        "ui": {"sidebar": "open", "tags": ["alpha", "beta"]},
    }
    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": payload},
    )

    assert response.status_code == 200
    assert response.json()["misc_settings"] == payload

    # Persisted across requests
    refetch = client_with_settings.get("/api/settings")
    assert refetch.status_code == 200
    assert refetch.json()["misc_settings"] == payload


def test_patch_settings_misc_settings_diff_is_deep_merged(client_with_settings):
    """Partial misc_settings_diff merges into the existing block.

    A diff that updates one nested field must NOT clobber sibling fields set
    by an earlier PATCH — the merge runs through the same ``_deep_merge``
    used for agent_settings / conversation_settings.
    """
    client_with_settings.patch(
        "/api/settings",
        json={
            "misc_settings_diff": {
                "theme": "dark",
                "ui": {"sidebar": "open", "density": "comfortable"},
            }
        },
    )

    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"ui": {"sidebar": "collapsed"}}},
    )

    assert response.status_code == 200
    misc = response.json()["misc_settings"]
    # Sibling top-level field is preserved
    assert misc["theme"] == "dark"
    # Updated nested field
    assert misc["ui"]["sidebar"] == "collapsed"
    # Sibling nested field is preserved (this is the deep-merge property)
    assert misc["ui"]["density"] == "comfortable"


def test_patch_settings_misc_settings_lists_replace_wholesale(client_with_settings):
    """Lists inside misc_settings are replaced wholesale, not merged."""
    client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"tags": ["alpha", "beta", "gamma"]}},
    )

    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"tags": []}},
    )

    assert response.status_code == 200
    assert response.json()["misc_settings"]["tags"] == []


def test_patch_settings_misc_settings_only_payload_is_accepted(client_with_settings):
    """misc_settings_diff alone satisfies the "at least one of" check."""
    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"theme": "dark"}},
    )

    assert response.status_code == 200


def test_patch_settings_misc_settings_accepts_arbitrary_payloads(client_with_settings):
    """The agent-server doesn't interpret misc_settings — any JSON shape is fine.

    Coverage for the *opaque* contract: a payload that would have been
    rejected by an inner typed schema (e.g. a string where a list would
    "naturally" go) is accepted and persisted verbatim, because validation
    of misc_settings is the frontend's responsibility.
    """
    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"tags": "not-a-list", "count": 42}},
    )

    assert response.status_code == 200
    misc = response.json()["misc_settings"]
    assert misc["tags"] == "not-a-list"
    assert misc["count"] == 42


def test_patch_settings_misc_settings_does_not_clobber_agent_settings(
    client_with_settings,
):
    """Writing only misc_settings must not reset agent_settings."""
    client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )

    response = client_with_settings.patch(
        "/api/settings",
        json={"misc_settings_diff": {"theme": "dark"}},
    )

    assert response.status_code == 200
    assert response.json()["agent_settings"]["llm"]["model"] == "gpt-4o"


def test_persisted_settings_v1_loads_with_empty_misc_settings(
    temp_persistence_dir, client_with_settings
):
    """A v1 settings file (no misc_settings) loads with empty defaults."""
    settings_path = temp_persistence_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "agent_settings": {
                    "agent_kind": "openhands",
                    "schema_version": AGENT_SETTINGS_SCHEMA_VERSION,
                    "llm": {"model": "gpt-4o"},
                },
                "conversation_settings": {
                    "schema_version": CONVERSATION_SETTINGS_SCHEMA_VERSION,
                },
                "active_profile": None,
            }
        )
    )

    response = client_with_settings.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["misc_settings"] == {}


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


# ── JSON Merge Patch (RFC 7386) unset semantics ─────────────────────────


def test_deep_merge_top_level_null_is_set_not_delete():
    """A ``None`` on a top-level *field* is left as-is (set), NOT deleted —
    so it flows to validation and fails loudly instead of silently resetting
    the field to its default."""
    merged = _deep_merge({"confirmation_mode": True}, {"confirmation_mode": None})
    assert merged == {"confirmation_mode": None}


def test_deep_merge_nested_null_deletes_entry():
    """A ``None`` *inside a nested map* removes that entry; siblings survive."""
    merged = _deep_merge(
        {"env": {"KEEP": "a", "DROP": "b"}},
        {"env": {"DROP": None}},
    )
    assert merged == {"env": {"KEEP": "a"}}


def test_deep_merge_nested_null_on_absent_key_is_noop():
    """Unsetting a nested key that isn't present is a no-op, not an error."""
    merged = _deep_merge({"env": {"KEEP": "a"}}, {"env": {"MISSING": None}})
    assert merged == {"env": {"KEEP": "a"}}


def test_deep_merge_new_map_embedded_null_stored_as_is():
    """Corner case (documented in ``_deep_merge``): when the nested map itself
    doesn't exist in base yet, the overlay dict is assigned wholesale — null
    entries inside are NOT treated as deletes (can't delete from a map that
    doesn't exist yet). Pins the guarantee against a future refactor."""
    merged = _deep_merge({}, {"new_map": {"KEY": None}})
    assert merged == {"new_map": {"KEY": None}}


def test_deep_merge_non_null_still_wins():
    """Regression: non-null values still set/overwrite and merge as before."""
    merged = _deep_merge({"a": 1, "b": {"x": 1}}, {"a": 2, "b": {"y": 2}})
    assert merged == {"a": 2, "b": {"x": 1, "y": 2}}


# ── apply_agent_settings_diff parity (PersistedSettings.update) ─────────


def test_update_agent_settings_same_kind_merge() -> None:
    """Same-kind update deep-merges within the variant."""
    settings = PersistedSettings()
    settings.update({"agent_settings_diff": {"llm": {"model": "gpt-4o"}}})
    assert settings.agent_settings.llm.model == "gpt-4o"


def test_update_agent_settings_kind_switch_replaces_fresh() -> None:
    """Kind switch starts from fresh variant; old fields are not carried."""
    settings = PersistedSettings()
    settings.update({"agent_settings_diff": {"llm": {"model": "gpt-x"}}})

    settings.update(
        {"agent_settings_diff": {"agent_kind": "acp", "acp_server": "claude-code"}}
    )

    assert isinstance(settings.agent_settings, ACPAgentSettings)
    assert settings.agent_settings.acp_server == "claude-code"


def test_update_agent_settings_switch_back_to_openhands() -> None:
    """Switching back to openhands starts fresh; ACP fields are not leaked."""
    from openhands.sdk.settings.model import OpenHandsAgentSettings

    settings = PersistedSettings()
    settings.update(
        {"agent_settings_diff": {"agent_kind": "acp", "acp_server": "gemini-cli"}}
    )

    settings.update(
        {"agent_settings_diff": {"agent_kind": "openhands", "llm": {"model": "gpt-4o"}}}
    )

    assert isinstance(settings.agent_settings, OpenHandsAgentSettings)
    assert settings.agent_settings.llm.model == "gpt-4o"


def test_update_agent_settings_null_unsets_optional_field() -> None:
    """A null value on an optional field resets it to default (RFC 7386 semantics)."""
    settings = PersistedSettings()
    settings.update(
        {
            "agent_settings_diff": {
                "agent_kind": "acp",
                "acp_server": "claude-code",
                "acp_model": "claude-opus-4-8",
            }
        }
    )
    assert isinstance(settings.agent_settings, ACPAgentSettings)
    assert settings.agent_settings.acp_model == "claude-opus-4-8"

    settings.update({"agent_settings_diff": {"acp_model": None}})
    assert isinstance(settings.agent_settings, ACPAgentSettings)
    assert settings.agent_settings.acp_model is None


def test_update_agent_settings_secret_survives_same_kind_merge() -> None:
    """An existing api_key in agent_settings is preserved across a same-kind update."""
    from pydantic import SecretStr

    settings = PersistedSettings()
    settings.update(
        {"agent_settings_diff": {"llm": {"model": "gpt-4o", "api_key": "sk-SECRET"}}}
    )

    settings.update({"agent_settings_diff": {"llm": {"temperature": 0.5}}})

    assert isinstance(settings.agent_settings.llm.api_key, SecretStr)
    assert settings.agent_settings.llm.api_key.get_secret_value() == "sk-SECRET"


def test_patch_settings_null_on_scalar_field_fails_loudly(client_with_settings):
    """Regression guard against the silent-reset footgun: ``null`` on a
    non-optional scalar like ``confirmation_mode`` is rejected (422), not
    silently reverted to its (unsafe) default."""
    response = client_with_settings.patch(
        "/api/settings",
        json={"conversation_settings_diff": {"confirmation_mode": None}},
    )
    assert response.status_code == 422


def test_patch_settings_switch_agent_kind_from_acp_to_openhands(
    client_with_settings, temp_persistence_dir
):
    """PATCH /api/settings can switch from ACP to OpenHands.

    When ``agent_kind`` changes, incompatible fields from the old variant
    (like ``acp_command``) must not be merged into the new variant.
    This is a variant replacement, not a field merge."""
    # Seed with ACP settings, including a NON-default ``llm`` model. ``llm`` is
    # a field both variants share, so this lets us prove it is NOT silently
    # carried into the new variant on a switch.
    acp = ACPAgentSettings(
        acp_command=["echo", "test"],
        llm=LLM(model="acp-only-model", usage_id="default"),
    )
    persisted = PersistedSettings(agent_settings=acp)
    payload = persisted.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    _write_settings_file(temp_persistence_dir, payload)

    # Verify it starts as ACP with the seeded model.
    get_response = client_with_settings.get("/api/settings")
    seeded = get_response.json()["agent_settings"]
    assert seeded["agent_kind"] == "acp"
    assert seeded["llm"]["model"] == "acp-only-model"

    # Switch to OpenHands, restating ``llm`` with a new model.
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_kind": "openhands",
                "llm": {"model": "claude-3-5-sonnet-20241022"},
            }
        },
    )

    # Should succeed — no validation error about leftover ACP-specific fields
    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["agent_kind"] == "openhands"
    # ACP-specific fields should not appear in the response
    assert "acp_command" not in body["agent_settings"]
    # The restated ``llm`` model wins — the ACP-seeded value is gone.
    assert body["agent_settings"]["llm"]["model"] == "claude-3-5-sonnet-20241022"


def test_patch_settings_switch_drops_shared_field_when_not_restated(
    client_with_settings, temp_persistence_dir
):
    """A shared field (``llm``) set on the OLD variant is dropped on a kind
    switch unless the caller restates it — it falls back to the new variant's
    default rather than silently carrying over.

    This pins the intentional "fresh start on the new variant" contract: a
    kind switch is a variant replacement, so shared fields are not inherited.
    Callers that want to preserve a shared field must include it in the switch
    payload (see the sibling test, which restates ``llm``)."""
    # Seed ACP with a non-default llm model.
    acp = ACPAgentSettings(
        acp_command=["echo", "test"],
        llm=LLM(model="acp-only-model", usage_id="default"),
    )
    persisted = PersistedSettings(agent_settings=acp)
    payload = persisted.model_dump(mode="json", context={"expose_secrets": "plaintext"})
    _write_settings_file(temp_persistence_dir, payload)

    # Switch to OpenHands WITHOUT restating llm.
    response = client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"agent_kind": "openhands"}},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["agent_kind"] == "openhands"
    # The ACP-seeded model is NOT carried over; llm falls back to the
    # OpenHands variant's default model.
    default_model = OpenHandsAgentSettings().llm.model
    assert body["agent_settings"]["llm"]["model"] == default_model
    assert body["agent_settings"]["llm"]["model"] != "acp-only-model"


def test_patch_settings_switch_agent_kind_from_openhands_to_acp(client_with_settings):
    """PATCH /api/settings can switch from OpenHands to ACP.

    When switching to ACP, the new variant's required fields should be set
    without interference from the old variant's fields."""
    # Seed with OpenHands settings (default)
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "llm": {"model": "claude-3-5-sonnet-20241022"},
            }
        },
    )
    assert response.status_code == 200

    # Switch to ACP
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_kind": "acp",
                "acp_command": ["echo", "hello"],
            }
        },
    )

    # Should succeed — no validation error about leftover OpenHands-specific fields
    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["agent_kind"] == "acp"
    assert body["agent_settings"]["acp_command"] == ["echo", "hello"]


def test_patch_settings_same_kind_restated_still_deep_merges(client_with_settings):
    """Re-stating the current ``agent_kind`` is NOT a variant switch: the diff
    must still deep-merge so unrelated fields survive.

    ``new_kind != old_kind`` is False when the kind is restated, so the
    deep-merge branch runs. This pins that a client which echoes back the
    current ``agent_kind`` alongside an incremental edit does not accidentally
    trigger a full variant replacement (which would reset sibling fields)."""
    # Establish a model on the default OpenHands variant.
    response = client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"llm": {"model": "gpt-4o"}}},
    )
    assert response.status_code == 200

    # Restate agent_kind=openhands while setting only the api_key. Because the
    # kind is unchanged, this deep-merges and the model must be preserved.
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_kind": "openhands",
                "llm": {"api_key": "sk-test-key"},
            }
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["agent_kind"] == "openhands"
    # The model set in the first PATCH survives — proving deep-merge ran.
    assert body["agent_settings"]["llm"]["model"] == "gpt-4o"
    assert body["llm_api_key_is_set"] is True


def test_patch_settings_same_kind_merge_after_a_switch(client_with_settings):
    """After a variant switch, subsequent same-kind PATCHes resume deep-merge.

    The switch itself is a replacement, but the newly active variant must
    behave like any other for incremental edits afterwards — a follow-up
    field edit must not wipe the fields set during the switch."""
    # Switch from default OpenHands to ACP, setting two ACP fields.
    response = client_with_settings.patch(
        "/api/settings",
        json={
            "agent_settings_diff": {
                "agent_kind": "acp",
                "acp_command": ["my-cli"],
                "acp_args": ["--foo"],
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["agent_settings"]["acp_command"] == ["my-cli"]

    # Same-kind follow-up: edit a different field. Deep-merge must preserve
    # acp_command and acp_args set during the switch.
    response = client_with_settings.patch(
        "/api/settings",
        json={"agent_settings_diff": {"acp_model": "some-model"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_settings"]["acp_command"] == ["my-cli"]
    assert body["agent_settings"]["acp_args"] == ["--foo"]
    assert body["agent_settings"]["acp_model"] == "some-model"


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
