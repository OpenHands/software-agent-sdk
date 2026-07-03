from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp.mcp_config import MCPConfig
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.mcp_oauth_store import (
    create_mcp_oauth_token_storage_factory,
)
from openhands.agent_server.persistence import (
    PersistedSettings,
    get_settings_store,
    reset_stores,
)
from openhands.sdk.utils.cipher import Cipher


def _encrypt(cipher: Cipher, value: str) -> str:
    encrypted = cipher.encrypt(SecretStr(value))
    assert encrypted is not None
    return encrypted


@pytest.mark.asyncio
async def test_mcp_oauth_token_storage_factory_persists_values_in_settings(
    tmp_path: Path,
):
    reset_stores()
    try:
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("mcp-oauth-test-key"),
        )
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={
                "mcp_config": MCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "superhuman": {
                                "url": "https://mcp.example.com/mcp",
                                "auth": "oauth",
                                "authentication": {
                                    "type": "oauth",
                                    "client_auth_method": "none",
                                },
                            }
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)
        factory = create_mcp_oauth_token_storage_factory(config)

        key = "https://mcp.example.com/mcp/tokens"
        value = {
            "access_token": "super-secret-token",
            "refresh_token": "refresh-token",
        }

        store = factory()
        await store.put(key=key, value=value, collection="mcp-oauth-token")

        reloaded_store = factory()
        assert (
            await reloaded_store.get(key=key, collection="mcp-oauth-token")
        ) == value

        on_disk_text = (tmp_path / ".openhands" / "settings.json").read_text()
        assert "super-secret-token" not in on_disk_text
        assert "refresh-token" not in on_disk_text

        on_disk = json.loads(on_disk_text)
        stored_value = on_disk["agent_settings"]["mcp_config"]["mcpServers"][
            "superhuman"
        ]["oauth_credentials"]["mcp-oauth-token"][key]["value"]
        assert stored_value["access_token"].startswith("gAAAA")
        assert stored_value["refresh_token"].startswith("gAAAA")

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            exclude_none=True, exclude_defaults=True
        )["mcpServers"]["superhuman"]
        assert server["oauth_credentials"]["mcp-oauth-token"][key]["value"] == value
    finally:
        reset_stores()


@pytest.mark.asyncio
async def test_mcp_oauth_token_storage_reads_legacy_double_encrypted_values(
    tmp_path: Path,
):
    reset_stores()
    try:
        secret_key = "mcp-oauth-test-key"
        cipher = Cipher(secret_key)
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr(secret_key),
        )
        key = "https://mcp.example.com/mcp/tokens"
        settings = PersistedSettings()
        settings.agent_settings = settings.agent_settings.model_copy(
            update={
                "mcp_config": MCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "superhuman": {
                                "url": "https://mcp.example.com/mcp",
                                "auth": "oauth",
                                "oauth_credentials": {
                                    "mcp-oauth-token": {
                                        key: {
                                            "value": {
                                                "access_token": _encrypt(
                                                    cipher, "super-secret-token"
                                                ),
                                                "refresh_token": _encrypt(
                                                    cipher, "refresh-token"
                                                ),
                                                "token_type": _encrypt(
                                                    cipher, "Bearer"
                                                ),
                                                "expires_in": 432000,
                                            },
                                            "expires_at": None,
                                        }
                                    }
                                },
                            }
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        # save() adds the at-rest encryption layer around the already-encrypted
        # values above, reproducing settings written by the previous install path.
        settings_store.save(settings)

        store = create_mcp_oauth_token_storage_factory(config)()

        assert await store.get(key=key, collection="mcp-oauth-token") == {
            "access_token": "super-secret-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
            "expires_in": 432000,
        }
    finally:
        reset_stores()
