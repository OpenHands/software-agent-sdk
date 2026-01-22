"""
Tests for MCP exception handler returning 502 Bad Gateway.

MCP errors indicate failures in external MCP services (user-configured
servers like SSE endpoints, stdio processes, etc.). Using 502 signals
that the agent-server itself is healthy but an upstream dependency failed.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.api import _add_exception_handlers
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.sdk.mcp.exceptions import MCPError, MCPTimeoutError


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    _add_exception_handlers(app)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def mock_conversation_service():
    """Create a mock ConversationService for testing."""
    return AsyncMock(spec=ConversationService)


def test_mcp_error_returns_502(client, mock_conversation_service):
    """Test that MCPError exceptions result in 502 Bad Gateway response."""
    error_message = "Failed to connect to MCP server"
    mock_conversation_service.start_conversation.side_effect = MCPError(error_message)

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "llm": {
                        "model": "test/model",
                        "api_key": "test-key",
                    }
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"] == "MCP service error"
        assert data["error_type"] == "MCPError"
        assert error_message in data["message"]
    finally:
        client.app.dependency_overrides.clear()


def test_mcp_timeout_error_returns_502_with_details(client, mock_conversation_service):
    """Test that MCPTimeoutError includes timeout and server info in response."""
    timeout = 30.0
    mcp_config = {
        "mcpServers": {
            "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
            "custom_sse": {"url": "https://example.com/sse"},
        }
    }
    error_message = (
        f"MCP tool listing timed out after {timeout} seconds.\n"
        "MCP servers configured: fetch, custom_sse"
    )
    mock_conversation_service.start_conversation.side_effect = MCPTimeoutError(
        error_message, timeout=timeout, config=mcp_config
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "llm": {
                        "model": "test/model",
                        "api_key": "test-key",
                    }
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"] == "MCP service error"
        assert data["error_type"] == "MCPTimeoutError"
        assert data["timeout"] == timeout
        assert set(data["mcp_servers"]) == {"fetch", "custom_sse"}
        assert "timed out" in data["message"]
    finally:
        client.app.dependency_overrides.clear()


def test_mcp_timeout_error_without_config(client, mock_conversation_service):
    """Test MCPTimeoutError without config still returns 502."""
    timeout = 15.0
    error_message = f"MCP operation timed out after {timeout} seconds"
    mock_conversation_service.start_conversation.side_effect = MCPTimeoutError(
        error_message, timeout=timeout, config=None
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "llm": {
                        "model": "test/model",
                        "api_key": "test-key",
                    }
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 502
        data = response.json()
        assert data["detail"] == "MCP service error"
        assert data["error_type"] == "MCPTimeoutError"
        assert data["timeout"] == timeout
        # Should not have mcp_servers key when config is None
        assert "mcp_servers" not in data
    finally:
        client.app.dependency_overrides.clear()


def test_non_mcp_error_returns_500(client, mock_conversation_service):
    """Test that non-MCP exceptions still return 500 Internal Server Error."""
    mock_conversation_service.start_conversation.side_effect = ValueError(
        "Some internal error"
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "llm": {
                        "model": "test/model",
                        "api_key": "test-key",
                    }
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 500
        data = response.json()
        assert data["detail"] == "Internal Server Error"
    finally:
        client.app.dependency_overrides.clear()


def test_mcp_error_response_does_not_leak_secrets(client, mock_conversation_service):
    """Test that MCP error response does not leak config secrets."""
    timeout = 30.0
    mcp_config = {
        "mcpServers": {
            "secure_server": {
                "url": "https://example.com/sse",
                "headers": {"Authorization": "Bearer secret-token-12345"},
            }
        }
    }
    error_message = "MCP connection failed"
    mock_conversation_service.start_conversation.side_effect = MCPTimeoutError(
        error_message, timeout=timeout, config=mcp_config
    )

    client.app.dependency_overrides[get_conversation_service] = (
        lambda: mock_conversation_service
    )

    try:
        response = client.post(
            "/api/conversations",
            json={
                "agent": {
                    "llm": {
                        "model": "test/model",
                        "api_key": "test-key",
                    }
                },
                "workspace": {"working_dir": "/tmp/test"},
            },
        )

        assert response.status_code == 502
        data = response.json()

        # Should only include server names, not full config
        assert "mcp_servers" in data
        assert data["mcp_servers"] == ["secure_server"]

        # Should NOT include sensitive config details
        response_str = str(data)
        assert "secret-token-12345" not in response_str
        assert "Authorization" not in response_str
        assert "Bearer" not in response_str
    finally:
        client.app.dependency_overrides.clear()
