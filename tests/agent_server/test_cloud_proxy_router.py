"""Tests for the cloud proxy router."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from openhands.agent_server.cloud_proxy_router import (
    _is_host_allowed,
    cloud_proxy_router,
)


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(cloud_proxy_router, prefix="/api")
    return app


def _make_test_client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestHostAllowlist:
    def test_allows_canonical_cloud_host(self):
        assert _is_host_allowed("https://app.all-hands.dev")

    def test_allows_subdomain_of_allowed_root(self):
        assert _is_host_allowed("https://eu.all-hands.dev")

    def test_rejects_loopback(self):
        assert not _is_host_allowed("http://localhost:8000")
        assert not _is_host_allowed("http://127.0.0.1")

    def test_rejects_private_ipv4_addresses(self):
        assert not _is_host_allowed("http://10.0.0.1")
        assert not _is_host_allowed("http://172.16.0.1")
        assert not _is_host_allowed("http://192.168.1.1")

    def test_rejects_link_local_addresses(self):
        # 169.254.169.254 is the AWS / GCP / Azure instance metadata service.
        assert not _is_host_allowed("http://169.254.169.254")

    def test_rejects_private_ipv6_addresses(self):
        assert not _is_host_allowed("http://[fc00::1]")
        assert not _is_host_allowed("http://[fe80::1]")
        assert not _is_host_allowed("http://[::1]")

    def test_rejects_private_ip_even_when_allowlisted(self, monkeypatch):
        # If an operator misconfigures the allowlist to include a private
        # IP, the IP-literal denylist must still block it.
        monkeypatch.setenv("OH_CLOUD_PROXY_ALLOWED_HOSTS", "10.0.0.1")
        assert not _is_host_allowed("http://10.0.0.1")

    def test_rejects_unrelated_host(self):
        assert not _is_host_allowed("https://evil.example.com")

    def test_rejects_non_http_scheme(self):
        assert not _is_host_allowed("file:///etc/passwd")
        assert not _is_host_allowed("ftp://app.all-hands.dev")

    def test_env_var_overrides_default_allowlist(self, monkeypatch):
        monkeypatch.setenv("OH_CLOUD_PROXY_ALLOWED_HOSTS", "example.com")
        assert _is_host_allowed("https://example.com")
        assert _is_host_allowed("https://api.example.com")
        # Default allowlist is fully replaced — all-hands.dev no longer matches.
        assert not _is_host_allowed("https://app.all-hands.dev")


@pytest.mark.asyncio
async def test_proxy_forwards_get_and_returns_upstream_json(monkeypatch):
    app = _build_app()
    upstream_payload = {
        "items": [{"id": "org-1", "name": "Personal"}],
        "current_org_id": "org-1",
    }
    captured: dict[str, object] = {}

    async def fake_forward(method, url, headers, json_body, raw_body, timeout_seconds):
        captured.update(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json_body": json_body,
                "raw_body": raw_body,
                "timeout": timeout_seconds,
            }
        )
        return httpx.Response(
            status_code=200,
            content=json.dumps(upstream_payload).encode(),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        "openhands.agent_server.cloud_proxy_router._forward_upstream",
        fake_forward,
    )

    async with _make_test_client(app) as client:
        response = await client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://app.all-hands.dev",
                "method": "GET",
                "path": "/api/organizations",
                "headers": {"Authorization": "Bearer test-token"},
            },
        )

    assert response.status_code == 200
    assert response.json() == upstream_payload
    assert captured["method"] == "GET"
    assert captured["url"] == "https://app.all-hands.dev/api/organizations"
    assert captured["headers"] == {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_proxy_propagates_upstream_error_status(monkeypatch):
    app = _build_app()
    error_body = {"detail": "invalid api key"}

    async def fake_forward(*args, **kwargs):  # noqa: ARG001
        return httpx.Response(
            status_code=401,
            content=json.dumps(error_body).encode(),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        "openhands.agent_server.cloud_proxy_router._forward_upstream",
        fake_forward,
    )

    async with _make_test_client(app) as client:
        response = await client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://app.all-hands.dev",
                "method": "GET",
                "path": "/api/organizations",
                "headers": {"Authorization": "Bearer bad"},
            },
        )

    assert response.status_code == 401
    assert response.json() == error_body


@pytest.mark.asyncio
async def test_proxy_rejects_disallowed_host():
    app = _build_app()
    async with _make_test_client(app) as client:
        response = await client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://evil.example.com",
                "method": "GET",
                "path": "/whatever",
            },
        )

    assert response.status_code == 403
    assert "not allowed" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_network_error(monkeypatch):
    app = _build_app()

    async def fake_forward(*args, **kwargs):  # noqa: ARG001
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        "openhands.agent_server.cloud_proxy_router._forward_upstream",
        fake_forward,
    )

    async with _make_test_client(app) as client:
        response = await client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://app.all-hands.dev",
                "method": "GET",
                "path": "/api/organizations",
            },
        )

    assert response.status_code == 502


@pytest.mark.asyncio
async def test_proxy_strips_upstream_set_cookie_and_cors_headers(monkeypatch):
    app = _build_app()

    async def fake_forward(*args, **kwargs):  # noqa: ARG001
        return httpx.Response(
            status_code=200,
            content=b'{"ok": true}',
            headers={
                "content-type": "application/json",
                "set-cookie": "session=secret; HttpOnly",
                "access-control-allow-origin": "*",
            },
        )

    monkeypatch.setattr(
        "openhands.agent_server.cloud_proxy_router._forward_upstream",
        fake_forward,
    )

    async with _make_test_client(app) as client:
        response = await client.post(
            "/api/cloud-proxy",
            json={
                "host": "https://app.all-hands.dev",
                "method": "GET",
                "path": "/api/organizations",
            },
        )

    assert response.status_code == 200
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}
    assert "access-control-allow-origin" not in {
        k.lower() for k in response.headers.keys()
    }
