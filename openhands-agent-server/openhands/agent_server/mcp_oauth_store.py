"""Settings-backed OAuth token storage for FastMCP MCP clients.

FastMCP's OAuth client reads and writes an ``AsyncKeyValue`` token store.
This adapter maps FastMCP's token/client-info/expiry keys onto OpenHands
settings so MCP OAuth credentials remain in the settings DataModel and use
the same encryption/redaction path as other settings secrets.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, SupportsFloat

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPOAuthState,
    MCPOAuthTokenStorageField,
    MCPServer,
)
from openhands.sdk.mcp.utils import create_mcp_tools


logger = get_logger(__name__)


class _OAuthKeySpec(NamedTuple):
    suffix: str
    collection: str
    field: MCPOAuthTokenStorageField


_OAUTH_KEY_SPECS: tuple[_OAuthKeySpec, ...] = (
    _OAuthKeySpec("/tokens", "mcp-oauth-token", "tokens"),
    _OAuthKeySpec("/client_info", "mcp-oauth-client-info", "client_info"),
    _OAuthKeySpec("/token_expiry", "mcp-oauth-token-expiry", "token_expires_at"),
)


def _server_url_from_fastmcp_key(key: str) -> str:
    """Extract FastMCP's server-url prefix from its OAuth token-store key."""
    for spec in _OAUTH_KEY_SPECS:
        if key.endswith(spec.suffix):
            return key[: -len(spec.suffix)].rstrip("/")
    return key.rsplit("/", 1)[0].rstrip("/")


def _server_url_matches_key(server_url: str, key: str) -> bool:
    return server_url.rstrip("/") == _server_url_from_fastmcp_key(key)


def _find_matching_oauth_server(
    mcp_config: dict[str, MCPServer],
    key: str,
) -> tuple[str, MCPServer, MCPOAuthAuthCredential] | None:
    for server_name, server in mcp_config.items():
        if server.url is None or not _server_url_matches_key(server.url, key):
            continue
        auth = server.auth
        if isinstance(auth, MCPOAuthAuthCredential):
            return server_name, server, auth
    return None


def _state_field_for_fastmcp_key(
    key: str,
    collection: str | None,
) -> MCPOAuthTokenStorageField | None:
    for spec in _OAUTH_KEY_SPECS:
        if collection == spec.collection and key.endswith(spec.suffix):
            return spec.field
    return None


class MCPSettingsOAuthTokenStore:
    """FastMCP OAuth token storage persisted inside settings MCP servers."""

    def _get_entry_sync(
        self, key: str, collection: str | None
    ) -> tuple[dict[str, Any] | None, float | None]:
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return None, None

        store = get_settings_store()
        settings = store.load()
        if settings is None:
            return None, None

        mcp_config = settings.agent_settings.mcp_config
        match = _find_matching_oauth_server(mcp_config, key)
        if match is None:
            return None, None
        _, _, auth = match
        return (auth.state or MCPOAuthState()).get_token_storage_value(field), None

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        value, _ = await asyncio.to_thread(self._get_entry_sync, key, collection)
        return value

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        return await asyncio.to_thread(self._get_entry_sync, key, collection)

    def _put_sync(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        del ttl
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return
        stored_value = copy.deepcopy(dict(value))

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            mcp_config = settings.agent_settings.mcp_config
            match = _find_matching_oauth_server(mcp_config, key)
            if match is None:
                logger.warning(
                    "Could not persist MCP OAuth state: no configured MCP "
                    "server matches FastMCP key %r",
                    key,
                )
                return settings

            server_name, server, auth = match

            state = (auth.state or MCPOAuthState()).with_token_storage_value(
                field, stored_value
            )
            updated_servers = dict(mcp_config)
            updated_servers[server_name] = server.model_copy(
                update={
                    "auth": auth.model_copy(
                        update={"state": state if state.has_values else None}
                    )
                }
            )
            settings.agent_settings = settings.agent_settings.model_copy(
                update={"mcp_config": updated_servers}
            )
            return settings

        get_settings_store().update(apply_update)

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._put_sync,
            key,
            value,
            collection=collection,
            ttl=ttl,
        )

    def _delete_sync(self, key: str, collection: str | None = None) -> bool:
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return False

        deleted = False

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            nonlocal deleted
            mcp_config = settings.agent_settings.mcp_config
            match = _find_matching_oauth_server(mcp_config, key)
            if match is None:
                return settings
            server_name, server, auth = match
            state, deleted = (
                auth.state or MCPOAuthState()
            ).without_token_storage_value(field)
            if not deleted:
                return settings
            updated_servers = dict(mcp_config)
            updated_servers[server_name] = server.model_copy(
                update={
                    "auth": auth.model_copy(
                        update={"state": state if state.has_values else None}
                    )
                }
            )
            settings.agent_settings = settings.agent_settings.model_copy(
                update={"mcp_config": updated_servers}
            )
            return settings

        get_settings_store().update(apply_update)
        return deleted

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        return await asyncio.to_thread(self._delete_sync, key, collection)

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(key, collection=collection) for key in keys]

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(key, collection=collection) for key in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        if len(keys) != len(values):
            raise ValueError("keys and values must have the same length")
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        deleted = 0
        for key in keys:
            if await self.delete(key, collection=collection):
                deleted += 1
        return deleted


class InMemoryMCPOAuthTokenStore:
    """In-memory store used by non-mutating MCP install probes."""

    def __init__(
        self,
        *,
        state: MCPOAuthState | None = None,
    ):
        self._state = state or MCPOAuthState()

    def export_state(self) -> MCPOAuthState:
        return self._state

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return None
        return self._state.get_token_storage_value(field)

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        return await self.get(key, collection=collection), None

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        del ttl
        field = _state_field_for_fastmcp_key(key, collection)
        if field is not None:
            self._state = self._state.with_token_storage_value(field, value)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return False
        state, deleted = self._state.without_token_storage_value(field)
        if deleted:
            self._state = state
        return deleted

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(key, collection=collection) for key in keys]

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(key, collection=collection) for key in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        if len(keys) != len(values):
            raise ValueError("keys and values must have the same length")
        for key, value in zip(keys, values, strict=True):
            await self.put(key, value, collection=collection, ttl=ttl)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        deleted = 0
        for key in keys:
            if await self.delete(key, collection=collection):
                deleted += 1
        return deleted


@dataclass(frozen=True, slots=True)
class SettingsBackedMCPToolProvider:
    """Create MCP tools with FastMCP OAuth state persisted in settings."""

    def create_tools(
        self, mcp_config: dict[str, MCPServer], timeout: float = 30.0
    ) -> MCPClient:
        return create_mcp_tools(
            mcp_config,
            timeout,
            mcp_oauth_token_storage=MCPSettingsOAuthTokenStore(),
        )


def create_settings_backed_mcp_tool_provider(
    config: Config,
) -> SettingsBackedMCPToolProvider:
    """Initialize settings storage and return the agent-server MCP provider."""
    get_settings_store(config)
    if config.secret_key is None:
        logger.warning(
            "Saving MCP OAuth state without encryption "
            "(no OH_SECRET_KEY configured). Configure OH_SECRET_KEY for "
            "production deployments."
        )
    return SettingsBackedMCPToolProvider()
