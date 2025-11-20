"""Tests for bash_router.py endpoints."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import Config
from openhands.agent_server.models import BashCommand


@pytest.fixture
def test_bash_service():
    """Create a BashEventService instance for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        yield BashEventService(
            bash_events_dir=temp_path / "bash_events",
        )


@pytest.fixture
def client():
    """Create a test client for the FastAPI app without authentication."""
    config = Config(session_api_keys=[])  # Disable authentication
    return TestClient(create_app(config))


@pytest.mark.asyncio
async def test_clear_all_bash_events_empty_storage():
    """Test clearing bash events when storage is empty."""
    with patch("openhands.agent_server.bash_router.bash_event_service") as mock_service:
        mock_service.clear_all_events = AsyncMock(return_value=0)

        config = Config(session_api_keys=[])  # Disable authentication
        client = TestClient(create_app(config))
        response = client.delete("/api/bash/bash_events")

        assert response.status_code == 200
        assert response.json() == {"cleared_count": 0}
        mock_service.clear_all_events.assert_called_once()


@pytest.mark.asyncio
async def test_clear_all_bash_events_with_data():
    """Test clearing bash events when storage contains data."""
    with patch("openhands.agent_server.bash_router.bash_event_service") as mock_service:
        mock_service.clear_all_events = AsyncMock(return_value=5)

        config = Config(session_api_keys=[])  # Disable authentication
        client = TestClient(create_app(config))
        response = client.delete("/api/bash/bash_events")

        assert response.status_code == 200
        assert response.json() == {"cleared_count": 5}
        mock_service.clear_all_events.assert_called_once()


@pytest.mark.asyncio
async def test_clear_all_bash_events_integration(test_bash_service):
    """Integration test for clearing bash events."""
    # Execute some commands to create events
    commands = [
        BashCommand(command='echo "first"', cwd="/tmp"),
        BashCommand(command='echo "second"', cwd="/tmp"),
    ]

    for cmd in commands:
        await test_bash_service.start_bash_command(cmd)

    # Wait for commands to complete
    import asyncio

    await asyncio.sleep(2)

    # Verify events exist before clearing
    page = await test_bash_service.search_bash_events()
    initial_count = len(page.items)
    assert initial_count > 0

    # Clear all events
    cleared_count = await test_bash_service.clear_all_events()
    assert cleared_count == initial_count

    # Verify events are gone
    page_after = await test_bash_service.search_bash_events()
    assert len(page_after.items) == 0
