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
        ]["auth"]["credentials"]["mcp-oauth-token"][key]["value"]
        assert stored_value["access_token"].startswith("gAAAA")
        assert stored_value["refresh_token"].startswith("gAAAA")

        loaded = settings_store.load()
        assert loaded is not None
        assert loaded.agent_settings.mcp_config is not None
        server = loaded.agent_settings.mcp_config.model_dump(
            exclude_none=True, exclude_defaults=True
        )["mcpServers"]["superhuman"]
        assert server["auth"]["credentials"]["mcp-oauth-token"][key]["value"] == value
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
        assert "credentials" not in server.get("auth", {})
    finally:
        reset_stores()
