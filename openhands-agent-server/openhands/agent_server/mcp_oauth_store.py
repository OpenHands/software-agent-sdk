"""Settings-backed OAuth token storage for MCP clients."""

from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

from fastmcp.mcp_config import MCPConfig
from key_value.aio.protocols import AsyncKeyValue

from openhands.agent_server.config import Config
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk.agent.base import MCPOAuthTokenStorageFactory
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

_DEFAULT_COLLECTION = "default"
_OAUTH_CREDENTIALS_FIELD = "oauth_credentials"
_FASTMCP_OAUTH_KEY_SUFFIXES = ("/tokens", "/client_info", "/token_expiry")


def _collection_name(collection: str | None) -> str:
    return collection or _DEFAULT_COLLECTION


def _server_url_from_fastmcp_key(key: str) -> str:
    """Extract FastMCP's server-url prefix from its OAuth token-store key."""
    for suffix in _FASTMCP_OAUTH_KEY_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)].rstrip("/")
    return key.rsplit("/", 1)[0].rstrip("/")


def _dump_mcp_config(settings: PersistedSettings) -> dict[str, Any]:
    mcp_config = getattr(settings.agent_settings, "mcp_config", None)
    if mcp_config is None:
        return {"mcpServers": {}}
    if isinstance(mcp_config, MCPConfig):
        return mcp_config.model_dump(exclude_none=True, exclude_defaults=True)
    if isinstance(mcp_config, dict):
        return copy.deepcopy(mcp_config)
    return {"mcpServers": {}}


def _set_mcp_config(settings: PersistedSettings, mcp_config: dict[str, Any]) -> None:
    settings.agent_settings = settings.agent_settings.model_copy(
        update={"mcp_config": MCPConfig.model_validate(mcp_config)}
    )


def _server_url_matches_key(server_url: str, key: str) -> bool:
    normalized_url = server_url.rstrip("/")
    target_url = _server_url_from_fastmcp_key(key)
    return normalized_url == target_url or key.startswith(f"{normalized_url}/")


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
        if server.get("auth") == "oauth":
            return server_name, server
    return None


def _entry_value_and_expiry(
    entry: Any, *, now: float
) -> tuple[dict[str, Any] | None, float | None]:
    if not isinstance(entry, dict):
        return None, None
    value = entry.get("value")
    if not isinstance(value, dict):
        return None, None
    expires_at = entry.get("expires_at")
    if isinstance(expires_at, (int, float)) and expires_at <= now:
        return None, None
    return (
        copy.deepcopy(value),
        float(expires_at) if isinstance(expires_at, (int, float)) else None,
    )


class MCPSettingsOAuthTokenStore:
    """FastMCP OAuth token storage persisted inside ``settings.mcp_config``.

    FastMCP stores tokens under keys derived from the remote MCP server URL.
    We attach those records to the matching persisted MCP server object under
    ``oauth_credentials`` so every setting required to start future
    conversations lives in the settings DataModel.
    """

    def __init__(self, *, now: Any = time.time):
        self._now = now

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
        credentials = server.get(_OAUTH_CREDENTIALS_FIELD)
        if not isinstance(credentials, dict):
            return None, None
        bucket = credentials.get(_collection_name(collection))
        if not isinstance(bucket, dict):
            return None, None
        value, expires_at = _entry_value_and_expiry(
            bucket.get(key), now=float(self._now())
        )
        return value, expires_at

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        value, _ = await asyncio.to_thread(self._get_entry_sync, key, collection)
        return value

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        value, expires_at = await asyncio.to_thread(
            self._get_entry_sync, key, collection
        )
        if value is None:
            return None, None
        if expires_at is None:
            return value, None
        return value, max(0.0, expires_at - float(self._now()))

    def _put_sync(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        expires_at = None if ttl is None else float(self._now()) + float(ttl)
        stored_value = copy.deepcopy(dict(value))
        collection_key = _collection_name(collection)

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            mcp_config = _dump_mcp_config(settings)
            match = _find_matching_server(mcp_config, key)
            if match is None:
                logger.warning(
                    "Could not persist MCP OAuth credentials: no configured MCP "
                    "server matches FastMCP key %r",
                    key,
                )
                return settings

            _, server = match
            credentials = server.setdefault(_OAUTH_CREDENTIALS_FIELD, {})
            if not isinstance(credentials, dict):
                credentials = {}
                server[_OAUTH_CREDENTIALS_FIELD] = credentials
            bucket = credentials.setdefault(collection_key, {})
            if not isinstance(bucket, dict):
                bucket = {}
                credentials[collection_key] = bucket
            bucket[key] = {"value": stored_value, "expires_at": expires_at}
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
        collection_key = _collection_name(collection)
        deleted = False

        def apply_update(settings: PersistedSettings) -> PersistedSettings:
            nonlocal deleted
            mcp_config = _dump_mcp_config(settings)
            match = _find_matching_server(mcp_config, key)
            if match is None:
                return settings
            _, server = match
            credentials = server.get(_OAUTH_CREDENTIALS_FIELD)
            if not isinstance(credentials, dict):
                return settings
            bucket = credentials.get(collection_key)
            if not isinstance(bucket, dict) or key not in bucket:
                return settings
            del bucket[key]
            deleted = True
            if not bucket:
                credentials.pop(collection_key, None)
            if not credentials:
                server.pop(_OAUTH_CREDENTIALS_FIELD, None)
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
    """In-memory store used by non-mutating MCP install probes.

    The probe endpoint cannot write to persisted settings before the user has
    accepted the install. This store lets FastMCP complete OAuth, then exports
    the captured records so the response can hand one complete MCP server object
    back to the client for normal settings persistence.
    """

    def __init__(
        self,
        *,
        credentials: dict[str, Any] | None = None,
        now: Any = time.time,
    ):
        self._now = now
        self._credentials: dict[str, dict[str, dict[str, Any]]] = (
            copy.deepcopy(credentials) if credentials is not None else {}
        )

    def export_credentials(self) -> dict[str, Any]:
        now = float(self._now())
        exported: dict[str, Any] = {}
        for collection, bucket in self._credentials.items():
            live_bucket = {}
            for key, entry in bucket.items():
                value, expires_at = _entry_value_and_expiry(entry, now=now)
                if value is None:
                    continue
                live_bucket[key] = {"value": value, "expires_at": expires_at}
            if live_bucket:
                exported[collection] = live_bucket
        return exported

    async def get(
        self, key: str, *, collection: str | None = None
    ) -> dict[str, Any] | None:
        value, _ = await self.ttl(key, collection=collection)
        return value

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        collection_key = _collection_name(collection)
        entry = self._credentials.get(collection_key, {}).get(key)
        value, expires_at = _entry_value_and_expiry(entry, now=float(self._now()))
        if value is None:
            return None, None
        if expires_at is None:
            return value, None
        return value, max(0.0, expires_at - float(self._now()))

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        expires_at = None if ttl is None else float(self._now()) + float(ttl)
        bucket = self._credentials.setdefault(_collection_name(collection), {})
        bucket[key] = {"value": copy.deepcopy(dict(value)), "expires_at": expires_at}

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        collection_key = _collection_name(collection)
        bucket = self._credentials.get(collection_key)
        if not bucket or key not in bucket:
            return False
        del bucket[key]
        if not bucket:
            self._credentials.pop(collection_key, None)
        return True

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
            "Saving MCP OAuth credentials without encryption "
            "(no OH_SECRET_KEY configured). Configure OH_SECRET_KEY for "
            "production deployments."
        )

    def factory() -> AsyncKeyValue:
        return MCPSettingsOAuthTokenStore()

    return factory
