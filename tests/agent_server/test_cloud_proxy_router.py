import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.cloud_proxy_router import cloud_proxy_router


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(cloud_proxy_router, prefix="/api")
    return TestClient(app)


def test_cloud_proxy_forwards_upstream_post_request():
    client = make_client()
    response = httpx.Response(
        200,
        json={"success": True},
        headers={"content-type": "application/json"},
        request=httpx.Request(
            "POST",
            "https://runtime.example.com/api/conversations/convo-1/events/respond_to_confirmation",
        ),
    )

    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)
    async_client.request = AsyncMock(return_value=response)

    with patch("httpx.AsyncClient", return_value=async_client):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "POST",
                "path": "/api/conversations/convo-1/events/respond_to_confirmation",
                "headers": {"X-Session-API-Key": "session-key"},
                "body": {"accept": True},
            },
        )

    assert result.status_code == 200
    assert result.json() == {"success": True}
    async_client.request.assert_awaited_once_with(
        "POST",
        "https://abc123.prod-runtime.all-hands.dev/api/conversations/convo-1/events/respond_to_confirmation",
        headers={"X-Session-API-Key": "session-key"},
        json={"accept": True},
    )


def test_cloud_proxy_rejects_non_absolute_host():
    client = make_client()

    result = client.post(
        "/api/cloud-proxy",
        json={
            "host": "/relative",
            "method": "GET",
            "path": "/api/server_info",
        },
    )

    assert result.status_code == 422


def test_cloud_proxy_rejects_non_runtime_host():
    client = make_client()

    result = client.post(
        "/api/cloud-proxy",
        json={
            "host": "https://example.com",
            "method": "GET",
            "path": "/api/server_info",
        },
    )

    assert result.status_code == 422


def test_cloud_proxy_rejects_runtime_host_resolving_to_loopback():
    client = make_client()

    with patch(
        "socket.getaddrinfo",
        return_value=[
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("127.0.0.1", 443),
            )
        ],
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "GET",
                "path": "/api/server_info",
            },
        )

    assert result.status_code == 400
    assert result.json()["detail"] == "host resolves to a non-public address"
