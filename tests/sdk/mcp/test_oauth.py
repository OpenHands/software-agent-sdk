"""Tests for ``MCPOAuth``: an expired token refreshes against the token endpoint
the authorization server advertises, registration asks for a token endpoint auth
method the server supports, and Basic-authenticated token requests leave
``client_id`` out of the body."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from key_value.aio.stores.memory import MemoryStore

from openhands.sdk.mcp.exceptions import MCPError
from openhands.sdk.mcp.oauth import MCPOAuth, NonInteractiveMCPOAuth


pytestmark = pytest.mark.filterwarnings("ignore:Using in-memory token storage")

MCP_URL = "https://gitlab.example/api/v4/mcp"
TOKEN_ENDPOINT = "https://gitlab.example/oauth/token"
REGISTRATION_ENDPOINT = "https://gitlab.example/oauth/register"
RESOURCE_METADATA = {
    "resource": MCP_URL,
    "authorization_servers": ["https://gitlab.example"],
}
SERVER_METADATA = {
    "issuer": "https://gitlab.example",
    "authorization_endpoint": "https://gitlab.example/oauth/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "registration_endpoint": REGISTRATION_ENDPOINT,
    "response_types_supported": ["code"],
}
CLIENT_INFO = {
    "client_id": "client-id",
    "redirect_uris": ["https://app.example/callback"],
    "token_endpoint_auth_method": "none",
}
BASIC_CLIENT_INFO = {
    **CLIENT_INFO,
    "client_secret": "client-secret",
    "token_endpoint_auth_method": "client_secret_basic",
}
POST_CLIENT_INFO = {
    **BASIC_CLIENT_INFO,
    "token_endpoint_auth_method": "client_secret_post",
}


async def _stored_state(
    expires_at: float | None, client_info: dict[str, Any] = CLIENT_INFO
) -> MemoryStore:
    """A store holding what a completed install leaves behind.

    ``expires_at=None`` stores the registered client without any tokens.
    """
    store = MemoryStore()
    if expires_at is not None:
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
        value=client_info,
        collection="mcp-oauth-client-info",
    )
    return store


def _authorization_server(
    metadata: dict[str, Any],
) -> Callable[[httpx.Request], httpx.Response]:
    """Serve discovery for ``metadata``; the MCP endpoint itself demands OAuth."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/mcp":
            return httpx.Response(401)
        if request.url.path == "/.well-known/oauth-protected-resource/api/v4/mcp":
            return httpx.Response(200, json=RESOURCE_METADATA)
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json=metadata)
        return httpx.Response(404)

    return handler


_discovery = _authorization_server(SERVER_METADATA)


def _oauth(
    store: MemoryStore,
    handler: Callable[[httpx.Request], httpx.Response],
    oauth_class: type[MCPOAuth] = MCPOAuth,
    **kwargs: Any,
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

    oauth = oauth_class(
        mcp_url=MCP_URL, token_storage=store, client_name="test", **kwargs
    )
    oauth.httpx_client_factory = factory
    return oauth, requested


def _consent(oauth: MCPOAuth) -> None:
    """Stand in for the browser: hand back a code for the state we were sent."""
    states: list[str] = []

    async def redirect(authorization_url: str) -> None:
        states.append(parse_qs(urlparse(authorization_url).query)["state"][0])

    async def callback() -> tuple[str, str | None]:
        return "auth-code", states[-1]

    oauth.context.redirect_handler = redirect
    oauth.context.callback_handler = callback


async def _first_request(oauth: MCPOAuth) -> httpx.Request:
    """The first request the auth flow emits for an MCP call."""
    flow = oauth.async_auth_flow(httpx.Request("POST", MCP_URL, json={}))
    try:
        return await flow.__anext__()
    finally:
        await flow.aclose()


async def _serve_flow(
    oauth: MCPOAuth, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Answer every request the auth flow emits for an MCP call."""
    flow = oauth.async_auth_flow(httpx.Request("POST", MCP_URL, json={}))
    try:
        request = await anext(flow)
        while True:
            request = await flow.asend(handler(request))
    except StopAsyncIteration:
        pass
    finally:
        await flow.aclose()


async def _request_to(
    oauth: MCPOAuth, handler: Callable[[httpx.Request], httpx.Response], url: str
) -> httpx.Request:
    """Drive the auth flow, answering with ``handler``, until it requests ``url``."""
    flow = oauth.async_auth_flow(httpx.Request("POST", MCP_URL, json={}))
    try:
        request = await anext(flow)
        while str(request.url) != url:
            response = handler(request)
            response.request = request
            request = await flow.asend(response)
        return request
    finally:
        await flow.aclose()


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


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


@pytest.mark.asyncio
async def test_non_interactive_client_fails_instead_of_authorizing_again():
    # Arrange: the server rejects the stored refresh token and then the
    # unauthenticated MCP call.
    store = await _stored_state(time.time() - 60)
    requested: list[str] = []

    def server(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if str(request.url) == TOKEN_ENDPOINT:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if str(request.url) == MCP_URL:
            return httpx.Response(401)
        return _discovery(request)

    oauth, _ = _oauth(store, server, NonInteractiveMCPOAuth)

    # Act
    with pytest.raises(MCPError):
        await _serve_flow(oauth, server)

    # Assert: no authorization request or client registration, and the
    # stored state is left for the user to replace by authorizing again.
    paths = {httpx.URL(url).path for url in requested}
    assert not paths & {"/oauth/authorize", "/register"}
    assert await store.get(key=f"{MCP_URL}/tokens", collection="mcp-oauth-token")
    assert await store.get(
        key=f"{MCP_URL}/client_info", collection="mcp-oauth-client-info"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("supported", "requested"),
    [
        (["client_secret_basic", "client_secret_post", "none"], "none"),
        (["client_secret_post", "client_secret_basic"], "client_secret_post"),
        (["private_key_jwt"], None),
    ],
)
async def test_registration_requests_a_supported_token_endpoint_auth_method(
    supported: list[str], requested: str | None
):
    # Arrange
    server = _authorization_server(
        {**SERVER_METADATA, "token_endpoint_auth_methods_supported": supported}
    )
    oauth, _ = _oauth(MemoryStore(), server)

    # Act
    registration = await _request_to(oauth, server, REGISTRATION_ENDPOINT)

    # Assert
    body = json.loads(registration.content)
    assert body.get("token_endpoint_auth_method") == requested
    assert body["client_name"] == "test"


@pytest.mark.asyncio
async def test_registration_keeps_an_explicitly_configured_auth_method():
    # Arrange
    server = _authorization_server(
        {
            **SERVER_METADATA,
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
        }
    )
    oauth, _ = _oauth(
        MemoryStore(),
        server,
        additional_client_metadata={
            "token_endpoint_auth_method": "client_secret_basic"
        },
    )

    # Act
    registration = await _request_to(oauth, server, REGISTRATION_ENDPOINT)

    # Assert
    body = json.loads(registration.content)
    assert body["token_endpoint_auth_method"] == "client_secret_basic"


@pytest.mark.asyncio
async def test_static_client_is_used_as_configured_without_registration():
    # Arrange: the server would hand out a public client, but ours is fixed.
    server = _authorization_server(
        {**SERVER_METADATA, "token_endpoint_auth_methods_supported": ["none"]}
    )
    oauth, _ = _oauth(
        MemoryStore(), server, client_id="static-id", client_secret="static-secret"
    )
    _consent(oauth)

    # Act: a registration attempt would hit the 404 and abort the flow.
    token = await _request_to(oauth, server, TOKEN_ENDPOINT)

    # Assert
    body = _form(token)
    assert "Authorization" not in token.headers
    assert body["client_id"] == ["static-id"]
    assert body["client_secret"] == ["static-secret"]


@pytest.mark.asyncio
async def test_basic_auth_token_exchange_sends_client_id_only_in_the_header():
    # Arrange
    oauth, _ = _oauth(await _stored_state(None, BASIC_CLIENT_INFO), _discovery)
    _consent(oauth)

    # Act
    token = await _request_to(oauth, _discovery, TOKEN_ENDPOINT)

    # Assert
    credentials = base64.b64encode(b"client-id:client-secret").decode()
    assert token.headers["Authorization"] == f"Basic {credentials}"
    body = _form(token)
    assert "client_id" not in body
    assert "client_secret" not in body
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["auth-code"]
    assert {"code_verifier", "redirect_uri"} <= body.keys()
    assert token.headers["Content-Length"] == str(len(token.content))


@pytest.mark.asyncio
async def test_basic_auth_refresh_sends_client_id_only_in_the_header():
    # Arrange
    oauth, _ = _oauth(
        await _stored_state(time.time() - 60, BASIC_CLIENT_INFO), _discovery
    )

    # Act
    request = await _first_request(oauth)

    # Assert
    assert str(request.url) == TOKEN_ENDPOINT
    assert request.headers["Authorization"].startswith("Basic ")
    body = _form(request)
    assert "client_id" not in body
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["stored-refresh-token"]
    assert request.headers["Content-Length"] == str(len(request.content))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_info", "secret"),
    [(POST_CLIENT_INFO, ["client-secret"]), (CLIENT_INFO, None)],
)
async def test_body_authenticated_clients_keep_client_id_in_the_body(
    client_info: dict[str, Any], secret: list[str] | None
):
    # Arrange
    oauth, _ = _oauth(await _stored_state(time.time() - 60, client_info), _discovery)

    # Act
    request = await _first_request(oauth)

    # Assert
    assert "Authorization" not in request.headers
    body = _form(request)
    assert body["client_id"] == ["client-id"]
    assert body.get("client_secret") == secret
