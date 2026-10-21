"""Settings-backed OAuth token storage for FastMCP MCP clients.

FastMCP's OAuth client reads and writes an ``AsyncKeyValue`` token store.
This adapter maps FastMCP's token/client-info/expiry keys onto OpenHands
settings so MCP OAuth credentials remain in the settings DataModel and use
the same encryption/redaction path as other settings secrets.
"""

from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, SupportsFloat

import httpx

from openhands.agent_server.config import Config, WebhookSpec
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.client import MCPClient
from openhands.sdk.mcp.config import (
    MCPOAuthAuthCredential,
    MCPOAuthState,
    MCPOAuthTokenStorageField,
    MCPServer,
)
from openhands.sdk.mcp.utils import (
    ToolsChangedCallback,
    ToolsReconciledCallback,
    create_mcp_tools,
)
from openhands.sdk.utils.cipher import Cipher


logger = get_logger(__name__)

_WRITE_BACK_TIMEOUT_SECONDS = 10.0


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
    """FastMCP OAuth token storage persisted inside settings MCP servers.

    ``seed_mcp_config`` covers OAuth servers that are not in this server's
    settings store, such as the ones a hosted deployment passes inline on the
    agent (their settings live on the remote app server): the ``auth.state``
    they carry is served to FastMCP, and tokens refreshed during the
    conversation are kept in memory for the lifetime of the store instead of
    being dropped. Servers found in settings always take precedence.

    ``write_back_webhooks`` are the app server callbacks this agent server
    posts conversation events to; a refreshed inline state is posted there
    too so the app server can persist it (see ``_write_back_seeded_state``).
    """

    def __init__(
        self,
        *,
        seed_mcp_config: Mapping[str, MCPServer] | None = None,
        cipher: Cipher | None = None,
        write_back_webhooks: Sequence[WebhookSpec] = (),
        session_api_key: str | None = None,
    ):
        self._seeded: dict[str, MCPOAuthState] = {}
        self._seeded_lock = threading.Lock()
        self._write_back_webhooks = tuple(write_back_webhooks)
        self._session_api_key = session_api_key
        for server in (seed_mcp_config or {}).values():
            if server.url is None or server.oauth_auth is None:
                continue
            state = server.initial_oauth_state(cipher=cipher) or MCPOAuthState()
            self._seeded[server.url.rstrip("/")] = state

    def _seeded_state(self, key: str) -> MCPOAuthState | None:
        with self._seeded_lock:
            return self._seeded.get(_server_url_from_fastmcp_key(key))

    def _get_entry_sync(
        self, key: str, collection: str | None
    ) -> tuple[dict[str, Any] | None, float | None]:
        field = _state_field_for_fastmcp_key(key, collection)
        if field is None:
            return None, None

        store = get_settings_store()
        settings = store.load()
        mcp_config = settings.agent_settings.mcp_config if settings else {}
        match = _find_matching_oauth_server(mcp_config, key)
        if match is None:
            seeded = self._seeded_state(key)
            if seeded is None:
                return None, None
            return seeded.get_token_storage_value(field), None
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
        seeded_updates: list[tuple[str, MCPOAuthState]] = []

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            mcp_config = settings.agent_settings.mcp_config
            match = _find_matching_oauth_server(mcp_config, key)
            if match is None:
                server_url = _server_url_from_fastmcp_key(key)
                with self._seeded_lock:
                    seeded = self._seeded.get(server_url)
                    if seeded is not None:
                        updated = seeded.with_token_storage_value(field, stored_value)
                        self._seeded[server_url] = updated
                        seeded_updates.append((server_url, updated))
                        return settings
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
        for server_url, state in seeded_updates:
            self._write_back_seeded_state(server_url, state)

    def _write_back_seeded_state(self, server_url: str, state: MCPOAuthState) -> None:
        """Post the refreshed state of an inline server to the app server.

        The settings of a server passed inline on the agent live on the app
        server that started the conversation. Without this the refreshed
        tokens die with the sandbox, and the next conversation starts from
        the previous refresh token, which providers that rotate refresh
        tokens have already revoked. Best effort: a failure only logs, and
        the in-memory overlay keeps serving the new tokens.
        """
        if not self._write_back_webhooks:
            return
        payload = {
            "server_url": server_url,
            "oauth_state": state.to_response().model_dump(
                mode="json", exclude_none=True
            ),
        }
        for spec in self._write_back_webhooks:
            headers = dict(spec.headers)
            if self._session_api_key:
                headers["X-Session-API-Key"] = self._session_api_key
            url = f"{spec.base_url.rstrip('/')}/mcp-oauth-state"
            try:
                response = httpx.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=_WRITE_BACK_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except httpx.HTTPError:
                logger.warning(
                    "Could not write back MCP OAuth state for %s to %s",
                    server_url,
                    url,
                    exc_info=True,
                )

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
                server_url = _server_url_from_fastmcp_key(key)
                with self._seeded_lock:
                    seeded = self._seeded.get(server_url)
                    if seeded is not None:
                        seeded, deleted = seeded.without_token_storage_value(field)
                        self._seeded[server_url] = seeded
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
    """Create MCP tools with FastMCP OAuth state persisted in settings.

    OAuth servers absent from settings (passed inline on the agent) fall back
    to the OAuth state they carry, and their refreshed state is posted back
    through ``webhooks``; see ``MCPSettingsOAuthTokenStore``.
    """

    cipher: Cipher | None = None
    webhooks: tuple[WebhookSpec, ...] = ()
    session_api_key: str | None = None

    def create_tools(
        self,
        mcp_config: dict[str, MCPServer],
        timeout: float = 30.0,
        *,
        on_tools_changed: ToolsChangedCallback | None = None,
        on_tools_reconciled: ToolsReconciledCallback | None = None,
    ) -> MCPClient:
        return create_mcp_tools(
            mcp_config,
            timeout,
            mcp_oauth_token_storage=MCPSettingsOAuthTokenStore(
                seed_mcp_config=mcp_config,
                cipher=self.cipher,
                write_back_webhooks=self.webhooks,
                session_api_key=self.session_api_key,
            ),
            on_tools_changed=on_tools_changed,
            on_tools_reconciled=on_tools_reconciled,
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
    return SettingsBackedMCPToolProvider(
        cipher=config.cipher,
        webhooks=tuple(config.webhooks),
        session_api_key=(
            config.session_api_keys[0] if config.session_api_keys else None
        ),
    )
