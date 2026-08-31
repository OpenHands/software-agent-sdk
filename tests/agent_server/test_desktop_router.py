"""Tests for the deprecated desktop router stub."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.desktop_router import DesktopUrlResponse, get_desktop_url


@pytest.fixture
def client():
    config = Config(session_api_keys=[])  # Disable authentication for tests
    app = create_app(config)
    return TestClient(app)


@pytest.mark.asyncio
async def test_get_desktop_url_always_503():
    with pytest.raises(HTTPException) as exc_info:
        await get_desktop_url()

    assert exc_info.value.status_code == 503


def test_desktop_url_route_is_marked_deprecated(client):
    schema = client.app.openapi()
    operation = schema["paths"]["/api/desktop/url"]["get"]
    assert operation["deprecated"] is True


def test_desktop_url_response_model():
    response = DesktopUrlResponse(url=None)
    assert response.model_dump() == {"url": None}
