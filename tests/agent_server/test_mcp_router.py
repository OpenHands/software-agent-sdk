"""Tests for MCP router endpoints."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.mcp_router import mcp_router
from openhands.agent_server.mcp_service import MCPService
from openhands.agent_server.models import (
    MCPServerToolInfo,
)


@pytest.fixture
def mcp_service(tmp_path):
    return MCPService(storage_dir=tmp_path / "mcp_servers")


@pytest.fixture
def client(mcp_service):
    app = FastAPI()
    app.state.mcp_service = mcp_service
    app.include_router(mcp_router, prefix="/api")
    return TestClient(app)


VALID_CONFIG = {
    "mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}
}

FAKE_TOOLS = [
    MCPServerToolInfo(name="fetch", description="Fetch a URL"),
]


class TestCreateMCPServer:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_create_server(self, mock_discover, client):
        response = client.post(
            "/api/mcp",
            json={"id": "test-fetch", "config": VALID_CONFIG},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "test-fetch"
        assert data["status"] == "active"
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "fetch"
        assert data["error"] is None

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_create_duplicate(self, mock_discover, client):
        client.post("/api/mcp", json={"id": "dup", "config": VALID_CONFIG})
        response = client.post("/api/mcp", json={"id": "dup", "config": VALID_CONFIG})
        assert response.status_code == 409

    def test_create_invalid_config(self, client):
        response = client.post("/api/mcp", json={"id": "bad", "config": {"foo": "bar"}})
        assert response.status_code == 409

    def test_create_invalid_id(self, client):
        response = client.post(
            "/api/mcp", json={"id": "has spaces!", "config": VALID_CONFIG}
        )
        assert response.status_code == 422

    def test_create_empty_id(self, client):
        response = client.post("/api/mcp", json={"id": "", "config": VALID_CONFIG})
        assert response.status_code == 422

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        side_effect=RuntimeError("connection failed"),
    )
    def test_create_with_discovery_failure(self, mock_discover, client):
        response = client.post("/api/mcp", json={"id": "fail", "config": VALID_CONFIG})
        # Server is created but in error state
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "error"
        assert "connection failed" in data["error"]


class TestListMCPServers:
    def test_list_empty(self, client):
        response = client.get("/api/mcp")
        assert response.status_code == 200
        assert response.json() == []

    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_list_servers(self, mock_discover, client):
        client.post("/api/mcp", json={"id": "s1", "config": VALID_CONFIG})
        client.post("/api/mcp", json={"id": "s2", "config": VALID_CONFIG})
        response = client.get("/api/mcp")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        ids = {s["id"] for s in data}
        assert ids == {"s1", "s2"}


class TestGetMCPServer:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_get_server(self, mock_discover, client):
        client.post("/api/mcp", json={"id": "test", "config": VALID_CONFIG})
        response = client.get("/api/mcp/test")
        assert response.status_code == 200
        assert response.json()["id"] == "test"

    def test_get_nonexistent(self, client):
        response = client.get("/api/mcp/nonexistent")
        assert response.status_code == 404


class TestUpdateMCPServer:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_update_server(self, mock_discover, client):
        client.post("/api/mcp", json={"id": "test", "config": VALID_CONFIG})

        new_config = {
            "mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}
        }
        response = client.patch("/api/mcp/test", json={"config": new_config})
        assert response.status_code == 200
        assert response.json()["config"] == new_config

    def test_update_nonexistent(self, client):
        response = client.patch("/api/mcp/nonexistent", json={"config": VALID_CONFIG})
        assert response.status_code == 404


class TestDeleteMCPServer:
    @patch(
        "openhands.agent_server.mcp_service._discover_tools",
        return_value=FAKE_TOOLS,
    )
    def test_delete_server(self, mock_discover, client):
        client.post("/api/mcp", json={"id": "test", "config": VALID_CONFIG})
        response = client.delete("/api/mcp/test")
        assert response.status_code == 200
        assert response.json()["success"] is True

        # Confirm it's gone
        response = client.get("/api/mcp/test")
        assert response.status_code == 404

    def test_delete_nonexistent(self, client):
        response = client.delete("/api/mcp/nonexistent")
        assert response.status_code == 404
