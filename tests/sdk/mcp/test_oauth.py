"""Tests for ``MCPOAuth``: an expired token refreshes against the token endpoint
the authorization server advertises, not the ``<origin>/token`` guess."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore

from openhands.sdk.mcp.oauth import MCPOAuth


pytestmark = pytest.mark.filterwarnings("ignore:Using in-memory token storage")

MCP_URL = "https://gitlab.example/api/v4/mcp"
TOKEN_ENDPOINT = "https://gitlab.example/oauth/token"
RESOURCE_METADATA = {
    "resource": MCP_URL,
    "authorization_servers": ["https://gitlab.example"],
}
SERVER_METADATA = {
    "issuer": "https://gitlab.example",
    "authorization_endpoint": "https://gitlab.example/oauth/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "response_types_supported": ["code"],
}


async def _stored_state(expires_at: float) -> MemoryStore:
    """A store holding what a completed install leaves behind."""
    store = MemoryStore()
    await store.put(
        key=f"{MCP_URL}/tokens",
        value={
            "access_token": "stored-access-token",
            "refresh_token": "stored-refresh-token",
            "token_type": "Bearer",
            "expires_in": 7200,
        },
        collection="mcp-oauth-token",
    )
    await store.put(
        key=f"{MCP_URL}/token_expiry",
        value={"expires_at": expires_at},
        collection="mcp-oauth-token-expiry",
    )
    await store.put(
        key=f"{MCP_URL}/client_info",
        value={
            "client_id": "client-id",
            "redirect_uris": ["https://app.example/callback"],
            "token_endpoint_auth_method": "none",
        },
        collection="mcp-oauth-client-info",
    )
    return store


def _discovery(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/.well-known/oauth-protected-resource/api/v4/mcp":
        return httpx.Response(200, json=RESOURCE_METADATA)
    if request.url.path == "/.well-known/oauth-authorization-server":
        return httpx.Response(200, json=SERVER_METADATA)
    return httpx.Response(404)


def _oauth(
    store: MemoryStore, handler: Callable[[httpx.Request], httpx.Response]
) -> tuple[MCPOAuth, list[str]]:
    """An ``MCPOAuth`` whose discovery client is served by ``handler``."""
    requested: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return handler(request)

    def factory(headers=None, timeout=None, auth=None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(recording),
            headers=headers,
            timeout=timeout,
            auth=auth,
        )

    oauth = MCPOAuth(mcp_url=MCP_URL, token_storage=store, client_name="test")
    oauth.httpx_client_factory = factory
    return oauth, requested


async def _first_request(oauth: MCPOAuth) -> httpx.Request:
    """The first request the auth flow emits for an MCP call."""
    flow = oauth.async_auth_flow(httpx.Request("POST", MCP_URL, json={}))
    try:
        return await flow.__anext__()
    finally:
        await flow.aclose()


@pytest.mark.asyncio
async def test_expired_token_refreshes_against_the_discovered_endpoint():
    # Arrange
    oauth, requested = _oauth(await _stored_state(time.time() - 60), _discovery)

    # Act
    request = await _first_request(oauth)

    # Assert
    assert (request.method, str(request.url)) == ("POST", TOKEN_ENDPOINT)
    assert b"grant_type=refresh_token" in request.content
    assert requested == [
        "https://gitlab.example/.well-known/oauth-protected-resource/api/v4/mcp",
        "https://gitlab.example/.well-known/oauth-authorization-server",
    ]


@pytest.mark.asyncio
async def test_refresh_falls_back_to_the_default_endpoint_when_discovery_fails():
    # Arrange
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    oauth, _ = _oauth(await _stored_state(time.time() - 60), unreachable)

    # Act
    request = await _first_request(oauth)

    # Assert: upstream's guess, exactly as without the discovery step.
    assert (request.method, str(request.url)) == (
        "POST",
        "https://gitlab.example/token",
    )
    assert b"grant_type=refresh_token" in request.content


@pytest.mark.asyncio
async def test_valid_token_is_sent_without_discovery_or_refresh():
    # Arrange
    oauth, requested = _oauth(await _stored_state(time.time() + 3600), _discovery)

    # Act
    request = await _first_request(oauth)

    # Assert
    assert str(request.url) == MCP_URL
    assert request.headers["Authorization"] == "Bearer stored-access-token"
    assert requested == []
