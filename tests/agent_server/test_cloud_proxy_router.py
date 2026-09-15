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


def public_dns_result(ip: str = "93.184.216.34"):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def mock_upstream(status_code: int = 200):
    response = httpx.Response(
        status_code,
        json={"success": True},
        headers={"content-type": "application/json"},
        request=httpx.Request(
            "POST",
            "https://abc123.prod-runtime.all-hands.dev/api/conversations/conv-1",
        ),
    )
    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=None)
    async_client.request = AsyncMock(return_value=response)
    return async_client


def test_cloud_proxy_forwards_confirmation_post():
    client = make_client()
    async_client = mock_upstream()

    with (
        patch("socket.getaddrinfo", return_value=public_dns_result()),
        patch("httpx.AsyncClient", return_value=async_client),
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "POST",
                "path": "/api/conversations/conv-1/events/respond_to_confirmation",
                "headers": {"X-Session-API-Key": "session-key"},
                "body": {"accept": True},
            },
        )

    assert result.status_code == 200
    assert result.json() == {"success": True}
    async_client.request.assert_awaited_once_with(
        "POST",
        "https://abc123.prod-runtime.all-hands.dev"
        "/api/conversations/conv-1/events/respond_to_confirmation",
        headers={"X-Session-API-Key": "session-key"},
        json={"accept": True},
    )


def test_cloud_proxy_forwards_compact_context_post():
    client = make_client()
    async_client = mock_upstream()

    with (
        patch("socket.getaddrinfo", return_value=public_dns_result()),
        patch("httpx.AsyncClient", return_value=async_client),
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "POST",
                "path": "/api/conversations/conv-1/condense",
                "headers": {"X-Session-API-Key": "session-key"},
            },
        )

    assert result.status_code == 200
    async_client.request.assert_awaited_once_with(
        "POST",
        "https://abc123.prod-runtime.all-hands.dev/api/conversations/conv-1/condense",
        headers={"X-Session-API-Key": "session-key"},
        json=None,
    )


def test_cloud_proxy_drops_transport_headers_before_forwarding():
    client = make_client()
    async_client = mock_upstream()

    with (
        patch("socket.getaddrinfo", return_value=public_dns_result()),
        patch("httpx.AsyncClient", return_value=async_client),
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "POST",
                "path": "/api/conversations/conv-1/condense",
                "headers": {
                    "Host": "not-the-runtime.example",
                    "Content-Length": "999",
                    "X-Session-API-Key": "session-key",
                },
            },
        )

    assert result.status_code == 200
    assert async_client.request.call_args.kwargs["headers"] == {
        "X-Session-API-Key": "session-key"
    }


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


def test_cloud_proxy_rejects_runtime_host_with_path_or_port():
    client = make_client()

    for host in (
        "https://abc123.prod-runtime.all-hands.dev:8443",
        "https://abc123.prod-runtime.all-hands.dev/other",
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": host,
                "method": "GET",
                "path": "/api/server_info",
            },
        )
        assert result.status_code == 422


def test_cloud_proxy_rejects_protocol_relative_path():
    client = make_client()

    result = client.post(
        "/api/cloud-proxy",
        json={
            "host": "https://abc123.prod-runtime.all-hands.dev",
            "method": "GET",
            "path": "//example.com/api/server_info",
        },
    )

    assert result.status_code == 422


def test_cloud_proxy_rejects_runtime_host_resolving_to_loopback():
    client = make_client()

    with patch("socket.getaddrinfo", return_value=public_dns_result("127.0.0.1")):
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


def test_cloud_proxy_rejects_runtime_host_resolving_to_link_local_metadata():
    client = make_client()

    with patch(
        "socket.getaddrinfo",
        return_value=public_dns_result("169.254.169.254"),
    ):
        result = client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://abc123.prod-runtime.all-hands.dev",
                "method": "GET",
                "path": "/latest/meta-data/",
            },
        )

    assert result.status_code == 400
    assert result.json()["detail"] == "host resolves to a non-public address"
