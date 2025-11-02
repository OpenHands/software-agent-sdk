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
    return BrowserObservation(
        output=[TextContent(text=output)], error=error, screenshot_data=screenshot_data
    )


def assert_browser_observation_success(
    observation: BrowserObservation, expected_output: str | None = None
):
    """Assert that a browser observation indicates success."""
    assert isinstance(observation, BrowserObservation)
    assert observation.error is None
    if expected_output:
        if isinstance(observation.output, str):
            output_text = observation.output
        else:
            output_text = "".join(
                [c.text for c in observation.output if isinstance(c, TextContent)]
            )
        assert expected_output in output_text


def assert_browser_observation_error(
    observation: BrowserObservation, expected_error: str | None = None
):
    """Assert that a browser observation contains an error."""
    assert isinstance(observation, BrowserObservation)
    assert observation.error is not None
    if expected_error:
        assert expected_error in observation.error
