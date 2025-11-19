"""Tests for VSCode router."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import (
    get_vscode_service as dep_get_vscode_service,
)


@pytest.fixture
def client():
    """Create a test client."""
    config = Config(session_api_keys=[], enable_vscode=False)  # Disable auth & VSCode
    app = create_app(config)
    return TestClient(app)


@pytest.fixture
def mock_vscode_service():
    """Mock VSCode service for testing."""
    with patch("openhands.agent_server.vscode_router.get_vscode_service") as mock:
        yield mock.return_value


def test_get_vscode_url_success(client, mock_vscode_service):
    """Test getting VSCode URL successfully."""
    mock_vscode_service.get_vscode_url.return_value = (
        "http://localhost:8001/?tkn=test-token&folder=/workspace"
    )

    client.app.dependency_overrides[dep_get_vscode_service] = (
        lambda: mock_vscode_service
    )
    try:
        response = client.get(
            "/api/vscode/url", params={"base_url": "http://localhost"}
        )
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["url"] == "http://localhost:8001/?tkn=test-token&folder=/workspace"
    mock_vscode_service.get_vscode_url.assert_called_once_with(
        "http://localhost", "workspace"
    )


def test_get_vscode_url_error(client, mock_vscode_service):
    """Test getting VSCode URL with service error."""
    mock_vscode_service.get_vscode_url.side_effect = Exception("Service error")

    client.app.dependency_overrides[dep_get_vscode_service] = (
        lambda: mock_vscode_service
    )
    try:
        response = client.get("/api/vscode/url")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 500


def test_get_vscode_status_running(client, mock_vscode_service):
    """Test getting VSCode status when running."""
    mock_vscode_service.is_running.return_value = True

    client.app.dependency_overrides[dep_get_vscode_service] = (
        lambda: mock_vscode_service
    )
    try:
        response = client.get("/api/vscode/status")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"running": True, "enabled": True}
    mock_vscode_service.is_running.assert_called_once()


def test_get_vscode_status_not_running(client, mock_vscode_service):
    """Test getting VSCode status when not running."""
    mock_vscode_service.is_running.return_value = False

    client.app.dependency_overrides[dep_get_vscode_service] = (
        lambda: mock_vscode_service
    )
    try:
        response = client.get("/api/vscode/status")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"running": False, "enabled": True}


def test_get_vscode_status_error(client, mock_vscode_service):
    """Test getting VSCode status with service error."""
    mock_vscode_service.is_running.side_effect = Exception("Service error")

    client.app.dependency_overrides[dep_get_vscode_service] = (
        lambda: mock_vscode_service
    )
    try:
        response = client.get("/api/vscode/status")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 500


def test_vscode_router_endpoints_integration(client):
    """Test VSCode router endpoints through the API using DI override."""
    with patch("openhands.agent_server.vscode_router.get_vscode_service") as _unused:
        # Create a mock service and inject via dependency override
        mock_service = _unused.return_value
        mock_service.get_vscode_url.return_value = (
            "http://localhost:8001/?tkn=integration-token"
        )
        mock_service.is_running.return_value = True

        client.app.dependency_overrides[dep_get_vscode_service] = lambda: mock_service
        try:
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
        finally:
            client.app.dependency_overrides.clear()


def test_vscode_router_endpoints_with_errors(client):
    """Test VSCode router endpoints with service errors."""
    with patch("openhands.agent_server.vscode_router.get_vscode_service") as _unused:
        mock_service = _unused.return_value
        mock_service.is_running.side_effect = Exception("Service down")

        client.app.dependency_overrides[dep_get_vscode_service] = lambda: mock_service
        try:
            # Test URL endpoint error (no URL provided by mock; router returns 500)
            response = client.get("/api/vscode/url")
            assert response.status_code == 500

            # Test status endpoint error
            response = client.get("/api/vscode/status")
            assert response.status_code == 500
        finally:
            client.app.dependency_overrides.clear()


def test_get_vscode_url_disabled(client):
    """Test getting VSCode URL when VSCode is disabled."""
    client.app.dependency_overrides[dep_get_vscode_service] = lambda: None
    try:
        response = client.get("/api/vscode/url")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 503


def test_get_vscode_status_disabled(client):
    """Test getting VSCode status when VSCode is disabled."""
    client.app.dependency_overrides[dep_get_vscode_service] = lambda: None
    try:
        response = client.get("/api/vscode/status")
    finally:
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "running": False,
        "enabled": False,
        "message": "VSCode is disabled in configuration",
    }


def test_vscode_router_disabled_integration(client):
    """Test VSCode router endpoints when VSCode is disabled."""
    client.app.dependency_overrides[dep_get_vscode_service] = lambda: None
    try:
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
    finally:
        client.app.dependency_overrides.clear()
