"""Tests for BrowserToolExecutor integration logic."""

from unittest.mock import AsyncMock, patch

from openhands.tools.browser_use.definition import (
    BrowserClickAction,
    BrowserGetStateAction,
    BrowserNavigateAction,
    BrowserObservation,
)
from openhands.tools.browser_use.impl import BrowserToolExecutor

from .conftest import (
    assert_browser_observation_error,
    assert_browser_observation_success,
)


def test_browser_executor_initialization():
    """Test that BrowserToolExecutor initializes correctly."""
    executor = BrowserToolExecutor()

    assert executor._config["headless"] is True
    assert executor._config["allowed_domains"] == []
    assert executor._initialized is False
    assert executor._server is not None
    assert executor._async_executor is not None


def test_browser_executor_config_passing():
    """Test that configuration is passed correctly."""
    executor = BrowserToolExecutor(
        session_timeout_minutes=60,
        headless=False,
        allowed_domains=["example.com", "test.com"],
        custom_param="value",
    )

    assert executor._config["headless"] is False
    assert executor._config["allowed_domains"] == ["example.com", "test.com"]
    assert executor._config["custom_param"] == "value"


@patch("openhands.tools.browser_use.impl.BrowserToolExecutor.navigate")
async def test_browser_executor_action_routing_navigate(
    mock_navigate, mock_browser_executor
):
    """Test that navigate actions are routed correctly."""
    mock_navigate.return_value = "Navigation successful"

    action = BrowserNavigateAction(url="https://example.com", new_tab=False)
    result = await mock_browser_executor._execute_action(action)

    mock_navigate.assert_called_once_with("https://example.com", False)
    assert_browser_observation_success(result, "Navigation successful")


@patch("openhands.tools.browser_use.impl.BrowserToolExecutor.click")
async def test_browser_executor_action_routing_click(mock_click, mock_browser_executor):
    """Test that click actions are routed correctly."""
    mock_click.return_value = "Click successful"

    action = BrowserClickAction(index=5, new_tab=True)
    result = await mock_browser_executor._execute_action(action)

    mock_click.assert_called_once_with(5, True)
    assert_browser_observation_success(result, "Click successful")


@patch("openhands.tools.browser_use.impl.BrowserToolExecutor.get_state")
async def test_browser_executor_action_routing_get_state(
    mock_get_state, mock_browser_executor
):
    """Test that get_state actions are routed correctly and return directly."""
    expected_observation = BrowserObservation.from_text(
        text="State retrieved", screenshot_data="base64data"
    )
    mock_get_state.return_value = expected_observation

    action = BrowserGetStateAction(include_screenshot=True)
    result = await mock_browser_executor._execute_action(action)

    mock_get_state.assert_called_once_with(True)
    assert result is expected_observation


async def test_browser_executor_unsupported_action_handling(mock_browser_executor):
    """Test handling of unsupported action types."""

    class UnsupportedAction:
        pass

    action = UnsupportedAction()
    result = await mock_browser_executor._execute_action(action)

    assert_browser_observation_error(result, "Unsupported action type")


@patch("openhands.tools.browser_use.impl.BrowserToolExecutor.navigate")
async def test_browser_executor_error_wrapping(mock_navigate, mock_browser_executor):
    """Test that exceptions are properly wrapped in BrowserObservation."""
    mock_navigate.side_effect = Exception("Browser error occurred")

    action = BrowserNavigateAction(url="https://example.com")
    result = await mock_browser_executor._execute_action(action)

    assert_browser_observation_error(result, "Browser operation failed")
    assert "Browser error occurred" in result.text


def test_browser_executor_async_execution(mock_browser_executor):
    """Test that async execution works through the call method."""
    with patch.object(
        mock_browser_executor, "_execute_action", new_callable=AsyncMock
    ) as mock_execute:
        expected_result = BrowserObservation.from_text(text="Test result")
        mock_execute.return_value = expected_result

        action = BrowserNavigateAction(url="https://example.com")
        result = mock_browser_executor(action)

        assert result is expected_result
        mock_execute.assert_called_once_with(action)


async def test_browser_executor_initialization_lazy(mock_browser_executor):
    """Test that browser session initialization is lazy."""
    assert mock_browser_executor._initialized is False

    await mock_browser_executor._ensure_initialized()

    assert mock_browser_executor._initialized is True
    mock_browser_executor._server._init_browser_session.assert_called_once()


async def test_browser_executor_initialization_idempotent(mock_browser_executor):
    """Test that initialization is idempotent."""
    await mock_browser_executor._ensure_initialized()
    await mock_browser_executor._ensure_initialized()

    # Should only be called once
    assert mock_browser_executor._server._init_browser_session.call_count == 1


async def test_start_recording_initializes_session(mock_browser_executor):
    """Test that start_recording initializes a recording session with correct state."""
    import tempfile
    from unittest.mock import AsyncMock

    from openhands.tools.browser_use.recording import RecordingSession

    # Set up mock CDP session that simulates successful rrweb loading
    mock_cdp_session = AsyncMock()
    mock_cdp_session.session_id = "test-session"
    mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
        side_effect=[
            # First call: wait for rrweb load (returns success)
            {"result": {"value": {"success": True}}},
            # Second call: start recording (returns started)
            {"result": {"value": {"status": "started"}}},
        ]
    )
    mock_cdp_session.cdp_client.send.Page.addScriptToEvaluateOnNewDocument = AsyncMock(
        return_value={"identifier": "script-1"}
    )

    mock_browser_session = AsyncMock()
    mock_browser_session.get_or_create_cdp_session = AsyncMock(
        return_value=mock_cdp_session
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a real RecordingSession and test its behavior
        # Use output_dir - start() will create a timestamped subfolder
        session = RecordingSession(output_dir=temp_dir)
        result = await session.start(mock_browser_session)

        # Verify the session state was properly initialized
        assert session.is_active is True
        assert result == "Recording started"
        assert session._scripts_injected is True
        # Verify a timestamped subfolder was created
        assert session.session_dir is not None
        assert session.session_dir.startswith(temp_dir)
        assert "recording-" in session.session_dir


async def test_stop_recording_returns_summary_with_event_counts():
    """Test that stop_recording returns accurate summary with event counts."""
    import json
    import os
    import tempfile
    from unittest.mock import AsyncMock

    from openhands.tools.browser_use.recording import RecordingSession

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a recording session in RECORDING state with some events
        session = RecordingSession()
        session._storage._session_dir = temp_dir
        session._is_recording = True
        session._scripts_injected = True

        # Pre-populate the event buffer with some events
        test_events = [{"type": 3, "timestamp": i, "data": {}} for i in range(25)]
        session._events.extend(test_events)

        # Set up mock CDP session for stop
        mock_cdp_session = AsyncMock()
        mock_cdp_session.session_id = "test-session"
        # Return additional events from the browser when stopping
        mock_cdp_session.cdp_client.send.Runtime.evaluate = AsyncMock(
            return_value={
                "result": {
                    "value": json.dumps(
                        {"events": [{"type": 3, "timestamp": 100, "data": {}}] * 17}
                    )
                }
            }
        )

        mock_browser_session = AsyncMock()
        mock_browser_session.get_or_create_cdp_session = AsyncMock(
            return_value=mock_cdp_session
        )

        # Stop recording
        result = await session.stop(mock_browser_session)

        # Verify the summary contains accurate counts
        assert "Recording stopped" in result
        assert "42 events" in result  # 25 buffered + 17 from browser
        assert "1 file(s)" in result
        assert temp_dir in result

        # Verify state transition
        assert session.is_active is False

        # Verify file was actually created with correct content
        files = os.listdir(temp_dir)
        assert len(files) == 1
        with open(os.path.join(temp_dir, files[0])) as f:
            saved_events = json.load(f)
        assert len(saved_events) == 42


async def test_stop_recording_without_active_session_returns_error():
    """Test that stop_recording returns error when not recording."""
    from unittest.mock import AsyncMock

    from openhands.tools.browser_use.recording import RecordingSession

    # Create a session that's not recording
    session = RecordingSession()
    assert session.is_active is False

    mock_browser_session = AsyncMock()

    result = await session.stop(mock_browser_session)

    assert "Error" in result
    assert "Not recording" in result
