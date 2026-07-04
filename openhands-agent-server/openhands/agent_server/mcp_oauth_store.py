"""Settings-backed OAuth token storage for MCP clients."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

from key_value.aio.protocols import AsyncKeyValue

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.agent.base import MCPOAuthTokenStorageFactory
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp.config import (
    MCPOAuthClientInfoState,
    MCPOAuthState,
    MCPOAuthTokenState,
    OpenHandsMCPConfig,
)


logger = get_logger(__name__)

_TOKEN_COLLECTION = "mcp-oauth-token"
_CLIENT_INFO_COLLECTION = "mcp-oauth-client-info"
_TOKEN_EXPIRY_COLLECTION = "mcp-oauth-token-expiry"

_TOKEN_KEY_SUFFIX = "/tokens"
_CLIENT_INFO_KEY_SUFFIX = "/client_info"
_TOKEN_EXPIRY_KEY_SUFFIX = "/token_expiry"
_FASTMCP_OAUTH_KEY_SUFFIXES = (
    _TOKEN_KEY_SUFFIX,
    _CLIENT_INFO_KEY_SUFFIX,
    _TOKEN_EXPIRY_KEY_SUFFIX,
)


def _server_url_from_fastmcp_key(key: str) -> str:
    """Extract FastMCP's server-url prefix from its OAuth token-store key."""
    for suffix in _FASTMCP_OAUTH_KEY_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)].rstrip("/")
    return key.rsplit("/", 1)[0].rstrip("/")


def _dump_mcp_config(settings: PersistedSettings) -> dict[str, Any]:
    mcp_config = settings.agent_settings.mcp_config
    if mcp_config is None:
        return {"mcpServers": {}}
    return mcp_config.model_dump(
        mode="json",
        context={"expose_secrets": "plaintext"},
        exclude_none=True,
        exclude_defaults=True,
    )


def _set_mcp_config(settings: PersistedSettings, mcp_config: dict[str, Any]) -> None:
    settings.agent_settings = settings.agent_settings.model_copy(
        update={"mcp_config": OpenHandsMCPConfig.model_validate(mcp_config)}
    )


def _server_url_matches_key(server_url: str, key: str) -> bool:
    return server_url.rstrip("/") == _server_url_from_fastmcp_key(key)


def _find_matching_server(
    mcp_config: dict[str, Any],
    key: str,
) -> tuple[str, dict[str, Any]] | None:
    servers = mcp_config.get("mcpServers")
    if not isinstance(servers, dict):
        return None

    for server_name, server in servers.items():
        if not isinstance(server, dict):
            continue
        server_url = server.get("url")
        if not isinstance(server_url, str) or not _server_url_matches_key(
            server_url, key
        ):
            continue
        auth = server.get("auth")
        if isinstance(auth, dict) and auth.get("strategy") == "oauth2":
            return server_name, server
    return None


def _state_model_to_dict(state: MCPOAuthState) -> dict[str, Any]:
    return state.model_dump(
        mode="json",
        context={"expose_secrets": "plaintext"},
        exclude_none=True,
        exclude_defaults=True,
    )


def _state_from_auth(auth: Mapping[str, Any]) -> MCPOAuthState:
    state = auth.get("state")
    if not isinstance(state, Mapping):
        return MCPOAuthState()
    return MCPOAuthState.model_validate(state)


def _state_has_values(state: dict[str, Any]) -> bool:
    return bool(
        state.get("tokens")
        or state.get("client_info")
        or state.get("token_expires_at") is not None
    )


def _get_state_value(
    state: MCPOAuthState,
    key: str,
    collection: str | None,
) -> dict[str, Any] | None:
    if collection == _TOKEN_COLLECTION and key.endswith(_TOKEN_KEY_SUFFIX):
        if state.tokens is None or state.tokens.access_token is None:
            return None
        return state.tokens.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )

    if (
        collection == _CLIENT_INFO_COLLECTION
        and key.endswith(_CLIENT_INFO_KEY_SUFFIX)
        and state.client_info is not None
    ):
        return state.client_info.model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )

    if (
        collection == _TOKEN_EXPIRY_COLLECTION
        and key.endswith(_TOKEN_EXPIRY_KEY_SUFFIX)
        and state.token_expires_at is not None
    ):
        return {"expires_at": state.token_expires_at}

    return None


def _put_state_value(
    state: dict[str, Any],
    key: str,
    value: Mapping[str, Any],
    collection: str | None,
) -> bool:
    if collection == _TOKEN_COLLECTION and key.endswith(_TOKEN_KEY_SUFFIX):
        state["tokens"] = MCPOAuthTokenState.model_validate(value).model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )
        return True

    if collection == _CLIENT_INFO_COLLECTION and key.endswith(_CLIENT_INFO_KEY_SUFFIX):
        state["client_info"] = MCPOAuthClientInfoState.model_validate(value).model_dump(
            mode="json",
            context={"expose_secrets": "plaintext"},
            exclude_none=True,
            exclude_defaults=True,
        )
        return True

    if collection == _TOKEN_EXPIRY_COLLECTION and key.endswith(
        _TOKEN_EXPIRY_KEY_SUFFIX
    ):
        expires_at = value.get("expires_at")
        state["token_expires_at"] = (
            float(expires_at) if isinstance(expires_at, int | float) else None
        )
        return True

    return False


def _delete_state_value(
    state: dict[str, Any],
    key: str,
    collection: str | None,
) -> bool:
    if collection == _TOKEN_COLLECTION and key.endswith(_TOKEN_KEY_SUFFIX):
        return state.pop("tokens", None) is not None
    if collection == _CLIENT_INFO_COLLECTION and key.endswith(_CLIENT_INFO_KEY_SUFFIX):
        return state.pop("client_info", None) is not None
    if collection == _TOKEN_EXPIRY_COLLECTION and key.endswith(
        _TOKEN_EXPIRY_KEY_SUFFIX
    ):
        return state.pop("token_expires_at", None) is not None
    return False


class MCPSettingsOAuthTokenStore:
    """FastMCP OAuth token storage persisted inside ``settings.mcp_config``."""

    def _get_entry_sync(
        self, key: str, collection: str | None
    ) -> tuple[dict[str, Any] | None, float | None]:
        store = get_settings_store()
        settings = store.load()
        if settings is None:
            return None, None

        match = _find_matching_server(_dump_mcp_config(settings), key)
        if match is None:
            return None, None
        _, server = match
        auth = server.get("auth")
        if not isinstance(auth, Mapping):
            return None, None
        return _get_state_value(_state_from_auth(auth), key, collection), None

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
        stored_value = copy.deepcopy(dict(value))

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            mcp_config = _dump_mcp_config(settings)
            match = _find_matching_server(mcp_config, key)
            if match is None:
                logger.warning(
                    "Could not persist MCP OAuth state: no configured MCP "
                    "server matches FastMCP key %r",
                    key,
                )
                return settings

            _, server = match
            auth = server.get("auth")
            if not isinstance(auth, dict):
                return settings

            state = _state_model_to_dict(_state_from_auth(auth))
            if not _put_state_value(state, key, stored_value, collection):
                return settings
            if _state_has_values(state):
                auth["state"] = state
            else:
                auth.pop("state", None)
            _set_mcp_config(settings, mcp_config)
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
        deleted = False

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            nonlocal deleted
            mcp_config = _dump_mcp_config(settings)
            match = _find_matching_server(mcp_config, key)
            if match is None:
                return settings
            _, server = match
            auth = server.get("auth")
            if not isinstance(auth, dict):
                return settings
            state = _state_model_to_dict(_state_from_auth(auth))
            deleted = _delete_state_value(state, key, collection)
            if not deleted:
                return settings
            if _state_has_values(state):
                auth["state"] = state
            else:
                auth.pop("state", None)
            _set_mcp_config(settings, mcp_config)
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
        state: dict[str, Any] | MCPOAuthState | None = None,
    ):
        self._state = (
            state
            if isinstance(state, MCPOAuthState)
            else MCPOAuthState.model_validate(state or {})
        )

    def export_state(self) -> dict[str, Any]:
        return _state_model_to_dict(self._state)

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        return _get_state_value(self._state, key, collection)

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
        state = _state_model_to_dict(self._state)
        if _put_state_value(state, key, value, collection):
            self._state = MCPOAuthState.model_validate(state)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        state = _state_model_to_dict(self._state)
        deleted = _delete_state_value(state, key, collection)
        if deleted:
            self._state = MCPOAuthState.model_validate(state)
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


def create_mcp_oauth_token_storage_factory(
    config: Config,
) -> MCPOAuthTokenStorageFactory:
    """Return a per-client factory for persistent MCP OAuth token storage."""
    get_settings_store(config)
    if config.secret_key is None:
        logger.warning(
            "Saving MCP OAuth state without encryption "
            "(no OH_SECRET_KEY configured). Configure OH_SECRET_KEY for "
            "production deployments."
        )

    def factory() -> AsyncKeyValue:
        return MCPSettingsOAuthTokenStore()

    return factory
