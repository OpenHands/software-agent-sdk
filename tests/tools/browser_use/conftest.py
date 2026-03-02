"""Shared test utilities for browser_use tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openhands.sdk.tool.schema import TextContent
from openhands.tools.browser_use.definition import BrowserObservation
from openhands.tools.browser_use.impl import BrowserToolExecutor


@pytest.fixture
def mock_browser_server():
    """Create a mock CustomBrowserUseServer."""
    server = MagicMock()
    server._init_browser_session = AsyncMock()
    server._inject_scripts_to_session = AsyncMock()
    return server


@pytest.fixture
def mock_browser_executor(mock_browser_server):
    """Create a BrowserToolExecutor with mocked server."""
    executor = BrowserToolExecutor()
    executor._server = mock_browser_server
    return executor


def create_mock_browser_response(
    output: str = "Success",
    error: str | None = None,
    screenshot_data: str | None = None,
):
    """Helper to create mock browser responses."""
    if error:
        return BrowserObservation.from_text(
            text=error, is_error=True, screenshot_data=screenshot_data
        )
    return BrowserObservation.from_text(text=output, screenshot_data=screenshot_data)


def assert_browser_observation_success(
    observation: BrowserObservation, expected_output: str | None = None
):
    """Assert that a browser observation indicates success."""
    assert isinstance(observation, BrowserObservation)
    assert observation.is_error is False
    if expected_output:
        if isinstance(observation.content, str):
            output_text = observation.content
        else:
            output_text = "".join(
                [c.text for c in observation.content if isinstance(c, TextContent)]
            )
        assert expected_output in output_text


def assert_browser_observation_error(
    observation: BrowserObservation, expected_error: str | None = None
):
    """Assert that a browser observation contains an error."""
    assert isinstance(observation, BrowserObservation)
    assert observation.is_error is True
    if expected_error:
        assert expected_error in observation.text
