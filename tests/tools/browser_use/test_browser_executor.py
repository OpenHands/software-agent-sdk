"""Tests for BrowserToolExecutor integration logic."""

import asyncio
import builtins
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
from urllib.request import urlopen

import pytest

from openhands.sdk.utils.async_executor import AsyncExecutor
from openhands.tools.browser_use.definition import (
    BrowserClickAction,
    BrowserGetStateAction,
    BrowserNavigateAction,
    BrowserObservation,
)
from openhands.tools.browser_use.impl import (
    DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS,
    BrowserToolExecutor,
)

from .conftest import (
    assert_browser_observation_error,
    assert_browser_observation_success,
)


class _ThreadedSlowServer(ThreadingHTTPServer):
    daemon_threads = True


class SlowServiceBrowserExecutor(BrowserToolExecutor):
    """Minimal browser executor that blocks on a live HTTP request."""

    def __init__(self, action_timeout_seconds: float):
        self._server = cast(Any, SimpleNamespace(_is_recording=False))
        self._config = {}
        self._initialized = True
        self._async_executor = AsyncExecutor()
        self._cleanup_initiated = False
        self._action_timeout_seconds = action_timeout_seconds
        self.full_output_save_dir = None
        self._consecutive_failures = 0

    async def navigate(self, url: str, new_tab: bool = False) -> str:
        del new_tab
        return await asyncio.to_thread(self._fetch_url, url)

    def close(self) -> None:
        return

    @staticmethod
    def _fetch_url(url: str) -> str:
        with urlopen(url, timeout=30) as response:
            return response.read().decode()


@pytest.fixture
def slow_service():
    """Serve an endpoint that stays pending long enough to trigger a timeout."""
    request_started = threading.Event()

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            request_started.set()
            time.sleep(5)
            body = b"slow response"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            _ = (format, args)
            return

    server = _ThreadedSlowServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        host = server.server_address[0]
        port = server.server_address[1]
        yield f"http://{host}:{port}", request_started
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_browser_executor_initialization():
    """Test that BrowserToolExecutor initializes correctly."""
    executor = BrowserToolExecutor()

    assert executor._config["headless"] is True
    assert executor._config["allowed_domains"] == []
    assert executor._initialized is False
    assert executor._server is not None
    assert executor._async_executor is not None
    assert executor._action_timeout_seconds == DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS


def test_browser_executor_config_passing():
    """Test that configuration is passed correctly."""
    executor = BrowserToolExecutor(
        session_timeout_minutes=60,
        headless=False,
        allowed_domains=["example.com", "test.com"],
        action_timeout_seconds=12.5,
        custom_param="value",
    )

    assert executor._config["headless"] is False
    assert executor._config["allowed_domains"] == ["example.com", "test.com"]
    assert executor._config["custom_param"] == "value"
    assert executor._action_timeout_seconds == 12.5


def test_browser_executor_rejects_non_positive_action_timeout():
    """Test that BrowserToolExecutor validates action timeouts."""
    with patch("openhands.tools.browser_use.impl.run_with_timeout"):
        with patch.object(BrowserToolExecutor, "_ensure_chromium_available"):
            with patch("openhands.tools.browser_use.impl.CustomBrowserUseServer"):
                with patch("openhands.tools.browser_use.impl.AsyncExecutor"):
                    with pytest.raises(
                        ValueError,
                        match="action_timeout_seconds must be greater than 0",
                    ):
                        BrowserToolExecutor(action_timeout_seconds=0)


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


def test_browser_executor_timeout_wrapping_live_service(slow_service):
    """Test that a live slow service timeout becomes a BrowserObservation."""
    slow_url, request_started = slow_service
    executor = SlowServiceBrowserExecutor(action_timeout_seconds=1)

    try:
        result = executor(BrowserNavigateAction(url=slow_url))
    finally:
        executor.close()

    assert request_started.wait(timeout=1), "The slow service was never queried"
    assert_browser_observation_error(result, "Browser operation failed")
    assert "timed out after 1 seconds" in result.text


def test_browser_executor_timeout_wrapping(mock_browser_executor):
    """Test that browser action timeouts return BrowserObservation errors."""
    mock_browser_executor._action_timeout_seconds = 7

    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ):
        action = BrowserNavigateAction(url="https://example.com")
        result = mock_browser_executor(action)

    assert_browser_observation_error(result, "Browser operation failed")
    assert "timed out after 7 seconds" in result.text


def test_issue_2412_consecutive_failures_reset_session(mock_browser_executor):
    """After MAX_CONSECUTIVE_FAILURES timeouts, the session should be reset.

    When a browser crashes, every subsequent action times out against
    the dead session. After enough consecutive failures the executor
    should set _initialized=False so the next call re-creates the
    browser session instead of looping on the dead one.

    See: https://github.com/OpenHands/software-agent-sdk/issues/2412
    """
    from openhands.tools.browser_use.impl import MAX_CONSECUTIVE_FAILURES

    mock_browser_executor._initialized = True

    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ):
        action = BrowserNavigateAction(url="https://example.com")

        # First (MAX_CONSECUTIVE_FAILURES - 1) failures should NOT reset
        for i in range(MAX_CONSECUTIVE_FAILURES - 1):
            result = mock_browser_executor(action)
            assert result.is_error is True
            assert mock_browser_executor._initialized is True, (
                f"Session reset too early on failure {i + 1}"
            )
            assert "reset" not in result.text.lower()

        # The next failure triggers the reset
        result = mock_browser_executor(action)
        assert result.is_error is True
        assert mock_browser_executor._initialized is False
        assert "reset" in result.text.lower()
        assert mock_browser_executor._consecutive_failures == 0


def test_issue_2412_success_resets_failure_counter(mock_browser_executor):
    """A successful action should reset the consecutive failure counter.

    See: https://github.com/OpenHands/software-agent-sdk/issues/2412
    """
    mock_browser_executor._initialized = True

    # Simulate 2 failures
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ):
        action = BrowserNavigateAction(url="https://example.com")
        mock_browser_executor(action)
        mock_browser_executor(action)

    assert mock_browser_executor._consecutive_failures == 2

    # Now a success
    success_result = BrowserObservation.from_text(text="OK")
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        return_value=success_result,
    ):
        result = mock_browser_executor(action)

    assert result.is_error is False
    assert mock_browser_executor._consecutive_failures == 0


def test_issue_2412_action_errors_do_not_trigger_reset(mock_browser_executor):
    """Regular action errors should NOT count toward crash detection.

    Only timeouts indicate a potentially dead browser. Errors like
    invalid selector or missing element are normal agent mistakes.

    See: https://github.com/OpenHands/software-agent-sdk/issues/2412
    """
    from openhands.tools.browser_use.impl import MAX_CONSECUTIVE_FAILURES

    mock_browser_executor._initialized = True

    error_result = BrowserObservation.from_text(text="Element not found", is_error=True)
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        return_value=error_result,
    ):
        action = BrowserNavigateAction(url="https://example.com")
        for _ in range(MAX_CONSECUTIVE_FAILURES + 1):
            mock_browser_executor(action)

    # Session should NOT be reset despite many action errors
    assert mock_browser_executor._initialized is True
    assert mock_browser_executor._consecutive_failures == 0


def test_issue_2412_degraded_timeout_after_failures(mock_browser_executor):
    """Degraded timeout kicks in after 2+ consecutive timeout failures.

    See: https://github.com/OpenHands/software-agent-sdk/issues/2412
    """
    from openhands.tools.browser_use.impl import DEGRADED_TIMEOUT_SECONDS

    mock_browser_executor._initialized = True
    mock_browser_executor._action_timeout_seconds = 300.0

    action = BrowserNavigateAction(url="https://example.com")

    # First call fails — uses normal timeout
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ) as mock_run:
        mock_browser_executor(action)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 300.0

    # Second call still uses normal timeout (degraded kicks in at 2+)
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ) as mock_run:
        mock_browser_executor(action)
        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 300.0

    # Third call should use degraded timeout for the action.
    # Note: the reset also calls run_async for cleanup, so we check
    # the first call (the action), not the last (the cleanup).
    with patch.object(
        mock_browser_executor._async_executor,
        "run_async",
        side_effect=builtins.TimeoutError(),
    ) as mock_run:
        mock_browser_executor(action)
        # First call is the action (degraded timeout),
        # second call may be cleanup (5s) if reset triggers.
        _, kwargs = mock_run.call_args_list[0]
        assert kwargs["timeout"] == DEGRADED_TIMEOUT_SECONDS


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
