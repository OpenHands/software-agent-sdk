"""Tests for mcp_oauth_router.py endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture
def client() -> TestClient:
    config = Config(session_api_keys=[])
    return TestClient(create_app(config), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# POST /api/mcp/oauth/start
# ---------------------------------------------------------------------------


def test_start_returns_504_for_unreachable_server(client: TestClient):
    """An unreachable MCP server should cause a 504 (auth URL timeout)
    or a 400 (connection error surfaced before the wait times out)."""
    response = client.post(
        "/api/mcp/oauth/start",
        json={
            "server_url": "http://127.0.0.1:1/definitely-not-listening",
            "server_name": "unreachable",
            "timeout": 5.0,
        },
    )
    # The flow should fail because the MCP server is unreachable.
    # Depending on timing, we get 400 (early failure) or 504 (URL timeout).
    assert response.status_code in (400, 504), response.text


def test_start_rejects_empty_server_url(client: TestClient):
    """Empty server_url should be rejected at the schema layer."""
    response = client.post(
        "/api/mcp/oauth/start",
        json={"server_url": "", "server_name": "empty"},
    )
    assert response.status_code == 422


def test_start_rejects_zero_timeout(client: TestClient):
    """Timeout must be > 0."""
    response = client.post(
        "/api/mcp/oauth/start",
        json={
            "server_url": "http://example.com/mcp",
            "timeout": 0,
        },
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/mcp/oauth/status/{flow_id}
# ---------------------------------------------------------------------------


def test_status_returns_404_for_unknown_flow(client: TestClient):
    """An unknown flow_id should return 404."""
    response = client.get("/api/mcp/oauth/status/nonexistent-flow-id")
    assert response.status_code == 404


def test_status_response_shape(client: TestClient):
    """The 404 response should have the expected structure."""
    response = client.get("/api/mcp/oauth/status/unknown")
    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
    assert "unknown" in body["detail"]
