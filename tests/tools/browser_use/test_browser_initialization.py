"""Tests for browser tool executor initialization and timeout handling."""

from unittest.mock import MagicMock, patch

import pytest

from openhands.tools.browser_use.impl import BrowserToolExecutor
from openhands.tools.utils.timeout import TimeoutError


class TestBrowserInitialization:
    """Test browser tool executor initialization."""

    def test_initialization_timeout_handling(self):
        """Test that initialization timeout is handled properly."""
        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.run_with_timeout",
                side_effect=TimeoutError("Timeout occurred"),
            ),
        ):
            with pytest.raises(Exception) as exc_info:
                BrowserToolExecutor(init_timeout_seconds=5)

            assert "Browser tool initialization timed out after 5s" in str(
                exc_info.value
            )

    def test_initialization_custom_timeout(self):
        """Test initialization with custom timeout."""
        mock_server = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
            patch("openhands.tools.browser_use.impl.run_with_timeout") as mock_timeout,
        ):
            BrowserToolExecutor(init_timeout_seconds=60)
            mock_timeout.assert_called_once()
            # Check that the timeout was passed correctly
            args, kwargs = mock_timeout.call_args
            assert args[1] == 60  # timeout_seconds parameter

    def test_initialization_default_timeout(self):
        """Test initialization with default timeout."""
        mock_server = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
            patch("openhands.tools.browser_use.impl.run_with_timeout") as mock_timeout,
        ):
            BrowserToolExecutor()
            mock_timeout.assert_called_once()
            # Check that the default timeout was used
            args, kwargs = mock_timeout.call_args
            assert args[1] == 30  # default init_timeout_seconds

    def test_initialization_config_passed_to_server(self):
        """Test that configuration is properly passed to server."""
        mock_server = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
        ):
            executor = BrowserToolExecutor(
                headless=False,
                allowed_domains=["example.com"],
                session_timeout_minutes=60,
                custom_param="test",
            )

            expected_config = {
                "headless": False,
                "allowed_domains": ["example.com"],
                "executable_path": "/usr/bin/chromium",
                "custom_param": "test",
            }

            assert executor._config == expected_config

    def test_initialization_server_creation_with_timeout(self):
        """Test that server is created with correct session timeout."""
        mock_server = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ) as mock_server_class,
        ):
            BrowserToolExecutor(session_timeout_minutes=45)

            mock_server_class.assert_called_once_with(session_timeout_minutes=45)

    def test_initialization_async_executor_created(self):
        """Test that async executor is properly created."""
        mock_server = MagicMock()
        mock_async_executor = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
            patch(
                "openhands.tools.browser_use.impl.AsyncExecutor",
                return_value=mock_async_executor,
            ),
        ):
            executor = BrowserToolExecutor()

            assert executor._async_executor is mock_async_executor
            assert executor._initialized is False

    def test_initialization_chromium_not_available(self):
        """Test initialization when Chromium is not available."""
        with patch.object(
            BrowserToolExecutor,
            "_ensure_chromium_available",
            side_effect=Exception("Chromium not found"),
        ):
            with pytest.raises(Exception) as exc_info:
                BrowserToolExecutor()

            # The exception should be wrapped in a timeout error message
            assert "Browser tool initialization timed out" in str(
                exc_info.value
            ) or "Chromium not found" in str(exc_info.value)

    def test_call_method_delegates_to_async_executor(self):
        """Test that __call__ method properly delegates to async executor."""
        mock_server = MagicMock()
        mock_async_executor = MagicMock()
        mock_action = MagicMock()
        expected_result = MagicMock()

        mock_async_executor.run_async.return_value = expected_result

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
            patch(
                "openhands.tools.browser_use.impl.AsyncExecutor",
                return_value=mock_async_executor,
            ),
        ):
            executor = BrowserToolExecutor()
            result = executor(mock_action)

            assert result is expected_result
            mock_async_executor.run_async.assert_called_once_with(
                executor._execute_action, mock_action, timeout=300.0
            )

    def test_call_method_timeout_configuration(self):
        """Test that __call__ method uses correct timeout."""
        mock_server = MagicMock()
        mock_async_executor = MagicMock()
        mock_action = MagicMock()

        with (
            patch.object(
                BrowserToolExecutor,
                "_ensure_chromium_available",
                return_value="/usr/bin/chromium",
            ),
            patch(
                "openhands.tools.browser_use.impl.CustomBrowserUseServer",
                return_value=mock_server,
            ),
            patch(
                "openhands.tools.browser_use.impl.AsyncExecutor",
                return_value=mock_async_executor,
            ),
        ):
            executor = BrowserToolExecutor()
            executor(mock_action)

            # Verify the timeout is set to 300.0 seconds (5 minutes)
            mock_async_executor.run_async.assert_called_once()
            args, kwargs = mock_async_executor.run_async.call_args
            assert kwargs["timeout"] == 300.0
