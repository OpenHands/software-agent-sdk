"""Tests for the server details router, including the /ready endpoint."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import openhands.agent_server.server_details_router as sdr
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


@pytest.fixture(autouse=True)
def reset_initialization_state():
    """Reset the asyncio.Event between tests to avoid state leakage."""
    sdr._initialization_complete = asyncio.Event()
    yield
    sdr._initialization_complete = asyncio.Event()


@pytest.fixture
def client():
    app = create_app(Config(static_files_path=None))
    return TestClient(app)


def test_alive_and_health_return_ok_status(client):
    """The liveness and health checks should share the same JSON payload."""
    for endpoint in ("/alive", "/health"):
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_health_accepts_text_plain_for_backward_compatibility(client):
    response = client.get("/health", headers={"accept": "text/plain"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "OK"


def test_ready_returns_503_before_init(client):
    """The /ready endpoint should return 503 while initialization is not complete."""
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "initializing"


def test_ready_returns_200_after_init(client):
    """The /ready endpoint should return 200 after mark_initialization_complete()."""
    sdr.mark_initialization_complete()
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_resets_after_new_event(client):
    """After resetting the event, /ready should return 503 again."""
    sdr.mark_initialization_complete()
    assert client.get("/ready").status_code == 200

    # Simulate a reset (e.g. for testing)
    sdr._initialization_complete = asyncio.Event()
    response = client.get("/ready")
    assert response.status_code == 503
