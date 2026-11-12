"""Tests for VSCode router."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import get_vscode_service
from openhands.agent_server.server_context import server_context_of
from openhands.agent_server.vscode_router import (
    get_vscode_status,
    get_vscode_url,
)
from openhands.agent_server.vscode_service import VSCodeService


@pytest.fixture
def app() -> FastAPI:
    """An app with authentication disabled."""
    return create_app(Config(session_api_keys=[]))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_vscode_service() -> MagicMock:
    """Mock VSCode service for testing."""
    return MagicMock(spec=VSCodeService)


@pytest.mark.asyncio
async def test_get_vscode_url_success(mock_vscode_service):
    """Test getting VSCode URL successfully."""
    mock_vscode_service.get_vscode_url.return_value = (
        "http://localhost:8001/?tkn=test-token&folder=/workspace"
    )

    response = await get_vscode_url(
        "http://localhost", vscode_service=mock_vscode_service
    )

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

    response = await get_vscode_url(vscode_service=mock_vscode_service)

    assert response.url == "http://localhost:19000/?tkn=test-token&folder=workspace"
    mock_vscode_service.get_vscode_url.assert_called_once_with(None, "workspace")


@pytest.mark.asyncio
async def test_get_vscode_url_error(mock_vscode_service):
    """Test getting VSCode URL with service error."""
    mock_vscode_service.get_vscode_url.side_effect = Exception("Service error")

    with pytest.raises(HTTPException) as exc_info:
        await get_vscode_url(vscode_service=mock_vscode_service)

    assert exc_info.value.status_code == 500
    assert "Failed to get VSCode URL" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_vscode_status_running(mock_vscode_service):
    """Test getting VSCode status when running."""
    mock_vscode_service.is_running.return_value = True

    response = await get_vscode_status(vscode_service=mock_vscode_service)

    assert response == {"running": True, "enabled": True}
    mock_vscode_service.is_running.assert_called_once()


@pytest.mark.asyncio
async def test_get_vscode_status_not_running(mock_vscode_service):
    """Test getting VSCode status when not running."""
    mock_vscode_service.is_running.return_value = False

    response = await get_vscode_status(vscode_service=mock_vscode_service)

    assert response == {"running": False, "enabled": True}


@pytest.mark.asyncio
async def test_get_vscode_status_error(mock_vscode_service):
    """Test getting VSCode status with service error."""
    mock_vscode_service.is_running.side_effect = Exception("Service error")

    with pytest.raises(HTTPException) as exc_info:
        await get_vscode_status(vscode_service=mock_vscode_service)

    assert exc_info.value.status_code == 500
    assert "Failed to get VSCode status" in str(exc_info.value.detail)


def test_vscode_router_endpoints_integration(app, client, mock_vscode_service):
    """Test VSCode router endpoints through the API."""
    app.dependency_overrides[get_vscode_service] = lambda: mock_vscode_service
    mock_vscode_service.get_vscode_url.return_value = (
        "http://localhost:8001/?tkn=integration-token"
    )
    mock_vscode_service.is_running.return_value = True

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


def test_vscode_router_endpoints_with_errors(app, client, mock_vscode_service):
    """Test VSCode router endpoints with service errors."""
    app.dependency_overrides[get_vscode_service] = lambda: mock_vscode_service
    mock_vscode_service.get_vscode_url.side_effect = Exception("Service down")
    mock_vscode_service.is_running.side_effect = Exception("Service down")

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
    with pytest.raises(HTTPException) as exc_info:
        await get_vscode_url(vscode_service=None)

    assert exc_info.value.status_code == 503
    assert "VSCode is disabled in configuration" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_get_vscode_status_disabled():
    """Test getting VSCode status when VSCode is disabled."""
    response = await get_vscode_status(vscode_service=None)

    assert response == {
        "running": False,
        "enabled": False,
        "message": "VSCode is disabled in configuration",
    }


def test_vscode_router_disabled_integration():
    """Test VSCode router endpoints when VSCode is disabled."""
    client = TestClient(create_app(Config(session_api_keys=[], enable_vscode=False)))

    # Test URL endpoint returns 503 when disabled
    response = client.get("/api/vscode/url")
    assert response.status_code == 503

    # Test status endpoint returns disabled status
    response = client.get("/api/vscode/status")
    assert response.status_code == 200
    data = response.json()
    assert data["running"] is False
    assert data["enabled"] is False
    assert "VSCode is disabled in configuration" in data["message"]


def test_each_app_has_the_vscode_service_of_its_own_config(tmp_path: Path):
    """One app's editor settings must not decide another app's."""

    def client_for(name: str, *, enable_vscode: bool, vscode_port: int) -> TestClient:
        config = Config(
            session_api_keys=[],
            conversations_path=tmp_path / name / "conversations",
            bash_events_dir=tmp_path / name / "bash",
            preload_tools=False,
            enable_vscode=enable_vscode,
            vscode_port=vscode_port,
        )
        return TestClient(create_app(config))

    def service_of(client: TestClient) -> VSCodeService | None:
        app = client.app
        assert isinstance(app, FastAPI)
        return server_context_of(app).vscode_service

    enabled = client_for("a", enable_vscode=True, vscode_port=18001)
    disabled = client_for("b", enable_vscode=False, vscode_port=18001)
    other_port = client_for("c", enable_vscode=True, vscode_port=18002)

    assert enabled.get("/api/vscode/status").json()["enabled"] is True
    assert disabled.get("/api/vscode/status").json()["enabled"] is False
    assert disabled.get("/api/vscode/url").status_code == 503

    first, second = service_of(enabled), service_of(other_port)
    assert first is not None and second is not None
    assert (first.port, second.port) == (18001, 18002)
    assert service_of(disabled) is None
