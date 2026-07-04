from __future__ import annotations

import json
from pathlib import Path

import pytest
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
from openhands.sdk.mcp.config import OpenHandsMCPConfig


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
                "mcp_config": OpenHandsMCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "superhuman": {
                                "url": "https://mcp.example.com/mcp",
                                "auth": {
                                    "strategy": "oauth2",
                                    "authentication": {
                                        "type": "oauth",
                                        "client_auth_method": "none",
                                    },
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
        client_info_key = "https://mcp.example.com/mcp/client_info"
        token_expiry_key = "https://mcp.example.com/mcp/token_expiry"
        value = {
            "access_token": "super-secret-token",
            "refresh_token": "refresh-token",
        }
        client_info = {
            "redirect_uris": ["http://127.0.0.1:64801/callback"],
            "client_id": "superhuman-client",
            "client_secret": "superhuman-client-secret",
        }
        token_expiry = {"expires_at": 12345.0}

        store = factory()
        await store.put(key=key, value=value, collection="mcp-oauth-token")
        await store.put(
            key=client_info_key,
            value=client_info,
            collection="mcp-oauth-client-info",
        )
        await store.put(
            key=token_expiry_key,
            value=token_expiry,
            collection="mcp-oauth-token-expiry",
        )

        reloaded_store = factory()
        assert (
            await reloaded_store.get(key=key, collection="mcp-oauth-token")
        ) == value
        assert (
            await reloaded_store.get(
                key=client_info_key,
                collection="mcp-oauth-client-info",
            )
        ) == client_info
        assert (
            await reloaded_store.get(
                key=token_expiry_key,
                collection="mcp-oauth-token-expiry",
            )
        ) == token_expiry

        on_disk_text = (tmp_path / ".openhands" / "settings.json").read_text()
        assert "super-secret-token" not in on_disk_text
        assert "refresh-token" not in on_disk_text
        assert "superhuman-client-secret" not in on_disk_text

        on_disk = json.loads(on_disk_text)
        stored_state = on_disk["agent_settings"]["mcp_config"]["mcpServers"][
            "superhuman"
        ]["auth"]["state"]
        stored_value = stored_state["tokens"]
        assert stored_value["access_token"].startswith("gAAAA")
        assert stored_value["refresh_token"].startswith("gAAAA")
        assert stored_state["client_info"]["client_secret"].startswith("gAAAA")
        assert stored_state["token_expires_at"] == 12345.0

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )["mcpServers"]["superhuman"]
        assert server["auth"]["state"] == {
            "tokens": value,
            "client_info": client_info,
            "token_expires_at": 12345.0,
        }
    finally:
        reset_stores()


@pytest.mark.asyncio
async def test_mcp_oauth_token_storage_does_not_attach_to_non_oauth_server(
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
                "mcp_config": OpenHandsMCPConfig.model_validate(
                    {
                        "mcpServers": {
                            "plain": {
                                "url": "https://mcp.example.com/mcp",
                            }
                        }
                    }
                )
            }
        )
        settings_store = get_settings_store(config)
        settings_store.save(settings)

        store = create_mcp_oauth_token_storage_factory(config)()

        await store.put(
            key="https://mcp.example.com/mcp/tokens",
            value={"access_token": "super-secret-token"},
            collection="mcp-oauth-token",
        )

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            exclude_none=True, exclude_defaults=True
        )["mcpServers"]["plain"]
        assert "auth" not in server
    finally:
        reset_stores()
