"""Tests for VSCode router."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.vscode_router import (
    get_vscode_status,
    get_vscode_url,
)


@pytest.fixture
def client():
    """Create a test client."""
    config = Config(session_api_keys=[])  # Disable authentication for tests
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def mock_vscode_service():
    """Mock VSCode service for testing."""
    with patch("openhands.agent_server.vscode_router.get_vscode_service") as mock:
        yield mock.return_value


@pytest.mark.asyncio
async def test_get_vscode_url_success(mock_vscode_service):
    """Test getting VSCode URL successfully."""
    mock_vscode_service.get_connection_token.return_value = "test-token"
    mock_vscode_service.get_vscode_url.return_value = (
        "http://localhost:8001/?tkn=test-token&folder=/workspace"
    )

    response = await get_vscode_url("http://localhost")

    assert response.url == "http://localhost:8001/?tkn=test-token&folder=/workspace"
    mock_vscode_service.get_vscode_url.assert_called_once_with(
        "http://localhost", "workspace"
    )


@pytest.mark.asyncio
async def test_get_vscode_url_default_uses_configured_port(mock_vscode_service):
    """Omitting base_url must defer to the service (None), not hardcode :8001.

    The service builds ``http://localhost:{self.port}`` when base_url is None,
    so clients that don't know the deployment topology receive the port the
    server actually binds (e.g. OH_VSCODE_PORT) rather than a fixed default.
    """
    mock_vscode_service.get_vscode_url.return_value = (
        "http://localhost:19000/?tkn=test-token&folder=workspace"
    )

    response = await get_vscode_url()

    assert response.url == "http://localhost:19000/?tkn=test-token&folder=workspace"
    mock_vscode_service.get_vscode_url.assert_called_once_with(None, "workspace")


@pytest.mark.asyncio
async def test_get_vscode_url_error(mock_vscode_service):
    """Test getting VSCode URL with service error."""
    mock_vscode_service.get_connection_token.side_effect = Exception("Service error")

    with pytest.raises(HTTPException) as exc_info:
        await get_vscode_url()

    assert exc_info.value.status_code == 500
    assert "Failed to get VSCode URL" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_vscode_status_running(mock_vscode_service):
    """Test getting VSCode status when running."""
    mock_vscode_service.is_running.return_value = True

    response = await get_vscode_status()

    assert response == {"running": True, "enabled": True}
    mock_vscode_service.is_running.assert_called_once()


@pytest.mark.asyncio
async def test_get_vscode_status_not_running(mock_vscode_service):
    """Test getting VSCode status when not running."""
    mock_vscode_service.is_running.return_value = False

    response = await get_vscode_status()

    assert response == {"running": False, "enabled": True}


@pytest.mark.asyncio
async def test_get_vscode_status_error(mock_vscode_service):
    """Test getting VSCode status with service error."""
    mock_vscode_service.is_running.side_effect = Exception("Service error")

    with pytest.raises(HTTPException) as exc_info:
        await get_vscode_status()

    assert exc_info.value.status_code == 500
    assert "Failed to get VSCode status" in str(exc_info.value.detail)


def test_vscode_router_endpoints_integration(client):
    """Test VSCode router endpoints through the API."""
    # Patch both the router import and the service module
    with (
        patch(
            "openhands.agent_server.vscode_router.get_vscode_service"
        ) as mock_service_getter,
        patch("openhands.agent_server.api.get_vscode_service") as mock_api_service,
    ):
        mock_service = mock_service_getter.return_value
        mock_service.get_vscode_url.return_value = (
            "http://localhost:8001/?tkn=integration-token"
        )
        mock_service.is_running.return_value = True

        # Mock the API service to avoid startup
        mock_api_service.return_value.start.return_value = True
        mock_api_service.return_value.stop.return_value = None

        # Test URL endpoint
        response = client.get("/api/vscode/url")
        assert response.status_code == 200
        data = response.json()
        assert data["url"] == "http://localhost:8001/?tkn=integration-token"

        # Test URL endpoint with custom base URL
        response = client.get("/api/vscode/url?base_url=http://example.com")
        assert response.status_code == 200

        # Test status endpoint
        response = client.get("/api/vscode/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is True


def test_vscode_router_endpoints_with_errors(client):
    """Test VSCode router endpoints with service errors."""
    # Patch both the router import and the service module
    with (
        patch(
            "openhands.agent_server.vscode_router.get_vscode_service"
        ) as mock_service_getter,
        patch("openhands.agent_server.api.get_vscode_service") as mock_api_service,
    ):
        mock_service = mock_service_getter.return_value
        mock_service.is_running.side_effect = Exception("Service down")

        # Mock the API service to avoid startup
        mock_api_service.return_value.start.return_value = True
        mock_api_service.return_value.stop.return_value = None

        # Test URL endpoint error
        response = client.get("/api/vscode/url")
        assert response.status_code == 500
        data = response.json()
        assert data["detail"] == "Internal Server Error"

        # Test status endpoint error
        response = client.get("/api/vscode/status")
        assert response.status_code == 500
        data = response.json()
        assert data["detail"] == "Internal Server Error"


@pytest.mark.asyncio
async def test_get_vscode_url_disabled():
    """Test getting VSCode URL when VSCode is disabled."""
    with patch(
        "openhands.agent_server.vscode_router.get_vscode_service"
    ) as mock_service:
        mock_service.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_vscode_url()

        assert exc_info.value.status_code == 503
        assert "VSCode is disabled in configuration" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_vscode_status_disabled():
    """Test getting VSCode status when VSCode is disabled."""
    with patch(
        "openhands.agent_server.vscode_router.get_vscode_service"
    ) as mock_service:
        mock_service.return_value = None

        response = await get_vscode_status()

        assert response == {
            "running": False,
            "enabled": False,
            "message": "VSCode is disabled in configuration",
        }


def test_vscode_router_disabled_integration(client):
    """Test VSCode router endpoints when VSCode is disabled."""
    with (
        patch(
            "openhands.agent_server.vscode_router.get_vscode_service"
        ) as mock_router_service,
        patch("openhands.agent_server.api.get_vscode_service") as mock_api_service,
    ):
        # Configure VSCode as disabled
        mock_router_service.return_value = None

        # Mock the API service to avoid startup
        mock_api_service.return_value = None

        # Test URL endpoint returns 503 when disabled
        response = client.get("/api/vscode/url")
        assert response.status_code == 503
        data = response.json()
        # The error message might be in different fields depending on FastAPI error
        # handling
        error_message = data.get("detail", data.get("message", ""))
        assert (
            "VSCode is disabled" in error_message
            or "Internal Server Error" in error_message
        )

        # Test status endpoint returns disabled status
        response = client.get("/api/vscode/status")
        assert response.status_code == 200
        data = response.json()
        assert data["running"] is False
        assert data["enabled"] is False
        assert "VSCode is disabled in configuration" in data["message"]
