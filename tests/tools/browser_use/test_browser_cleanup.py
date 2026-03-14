"""Tests for browser tool executor cleanup and resource management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openhands.tools.browser_use.impl import BrowserToolExecutor


class TestBrowserCleanup:
    """Test browser tool executor cleanup functionality."""

    @pytest.fixture
    def mock_executor(self):
        """Create a mock browser executor for testing."""
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
            executor._server = mock_server
            executor._async_executor = mock_async_executor
            return executor

    async def test_close_browser_when_initialized(self, mock_executor):
        """Test closing browser when it's initialized."""
        mock_executor._initialized = True
        mock_executor._server._close_browser = AsyncMock(return_value="Browser closed")

        result = await mock_executor.close_browser()

        assert result == "Browser closed"
        assert mock_executor._initialized is False
        mock_executor._server._close_browser.assert_called_once()

    async def test_close_browser_when_not_initialized(self, mock_executor):
        """Test closing browser when it's not initialized."""
        mock_executor._initialized = False

        result = await mock_executor.close_browser()

        assert result == "No browser session to close"
        assert (
            not hasattr(mock_executor._server, "_close_browser")
            or not mock_executor._server._close_browser.called
        )

    async def test_cleanup_calls_close_all_sessions(self, mock_executor):
        """Test cleanup calls _close_all_sessions to properly kill browser."""
        mock_executor._server._close_all_sessions = AsyncMock()

        await mock_executor.cleanup()

        mock_executor._server._close_all_sessions.assert_called_once()

    async def test_cleanup_falls_back_to_close_browser(self, mock_executor):
        """
        Test cleanup falls back to close_browser when _close_all_sessions is missing.
        """
        mock_executor._initialized = True
        mock_executor._server._close_browser = AsyncMock(return_value="Browser closed")
        # Remove _close_all_sessions so hasattr returns False
        del mock_executor._server._close_all_sessions

        await mock_executor.cleanup()

        mock_executor._server._close_browser.assert_called_once()

    async def test_cleanup_with_close_all_sessions_exception(self, mock_executor):
        """Test cleanup handles _close_all_sessions exception gracefully."""
        mock_executor._server._close_all_sessions = AsyncMock(
            side_effect=Exception("Close sessions failed")
        )

        # Should not raise exception, just log warning
        await mock_executor.cleanup()

        mock_executor._server._close_all_sessions.assert_called_once()

    def test_close_method_calls_cleanup(self, mock_executor):
        """Test that close method calls cleanup through async executor."""
        mock_executor._async_executor.run_async = MagicMock()

        mock_executor.close()

        mock_executor._async_executor.run_async.assert_called_once_with(
            mock_executor.cleanup, timeout=30.0
        )
        mock_executor._async_executor.close.assert_called_once()

    def test_close_method_handles_cleanup_exception(self, mock_executor):
        """Test that close method handles cleanup exceptions gracefully."""
        mock_executor._async_executor.run_async = MagicMock(
            side_effect=Exception("Cleanup failed")
        )

        # Should not raise exception
        mock_executor.close()

        mock_executor._async_executor.close.assert_called_once()

    def test_close_method_always_closes_async_executor(self, mock_executor):
        """Test that close method always closes async executor even on exception."""
        mock_executor._async_executor.run_async = MagicMock(
            side_effect=Exception("Cleanup failed")
        )
        mock_executor._async_executor.close = MagicMock()

        mock_executor.close()

        mock_executor._async_executor.close.assert_called_once()

    def test_del_method_calls_close(self, mock_executor):
        """Test that __del__ method calls close."""
        with patch.object(mock_executor, "close") as mock_close:
            mock_executor.__del__()
            mock_close.assert_called_once()

    def test_del_method_handles_close_exception(self, mock_executor):
        """Test that __del__ method handles close exceptions gracefully."""
        with patch.object(
            mock_executor, "close", side_effect=Exception("Close failed")
        ):
            # Should not raise exception
            mock_executor.__del__()

    def test_close_method_timeout_configuration(self, mock_executor):
        """Test that close method uses correct timeout for cleanup."""
        mock_executor._async_executor.run_async = MagicMock()

        mock_executor.close()

        # Verify the timeout is set to 30.0 seconds
        mock_executor._async_executor.run_async.assert_called_once()
        args, kwargs = mock_executor._async_executor.run_async.call_args
        assert kwargs["timeout"] == 30.0

    async def test_cleanup_not_initialized_browser(self, mock_executor):
        """Test cleanup when browser is not initialized."""
        mock_executor._initialized = False
        mock_executor._server._close_all_sessions = AsyncMock()

        await mock_executor.cleanup()

        # _close_all_sessions is still called (it's a no-op if no sessions exist)
        mock_executor._server._close_all_sessions.assert_called_once()
