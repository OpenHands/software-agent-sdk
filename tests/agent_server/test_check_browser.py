"""Tests for the check_browser functionality."""

from unittest.mock import MagicMock, patch


class TestCheckBrowser:
    """Test check_browser function with mocked browser components."""

    def test_check_browser_success(self, capsys):
        """Test check_browser returns True when browser works correctly."""
        mock_result = MagicMock()
        mock_result.is_error = False
        mock_result.content = "Success"

        mock_executor = MagicMock()
        mock_executor.return_value = mock_result

        with (
            patch(
                "openhands.tools.preset.default.register_default_tools"
            ) as mock_register,
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                return_value=mock_executor,
            ) as mock_executor_class,
        ):
            from openhands.agent_server.__main__ import check_browser

            result = check_browser()

            assert result is True
            mock_register.assert_called_once_with(enable_browser=True)
            mock_executor_class.assert_called_once_with(
                headless=True, session_timeout_minutes=2
            )
            mock_executor.close.assert_called_once()

            captured = capsys.readouterr()
            assert "Browser check passed" in captured.out

    def test_check_browser_failure_is_error(self, capsys):
        """Test check_browser returns False when result.is_error is True."""
        mock_result = MagicMock()
        mock_result.is_error = True
        mock_result.content = "Navigation failed"

        mock_executor = MagicMock()
        mock_executor.return_value = mock_result

        with (
            patch("openhands.tools.preset.default.register_default_tools"),
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                return_value=mock_executor,
            ),
        ):
            from openhands.agent_server.__main__ import check_browser

            result = check_browser()

            assert result is False
            mock_executor.close.assert_called_once()

            captured = capsys.readouterr()
            assert "Browser check failed" in captured.out
            assert "Navigation failed" in captured.out

    def test_check_browser_failure_exception(self, capsys):
        """Test check_browser returns False when an exception is raised."""
        mock_executor = MagicMock()
        mock_executor.side_effect = RuntimeError("Browser crashed")

        with (
            patch("openhands.tools.preset.default.register_default_tools"),
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                return_value=mock_executor,
            ),
        ):
            from openhands.agent_server.__main__ import check_browser

            result = check_browser()

            assert result is False
            mock_executor.close.assert_called_once()

            captured = capsys.readouterr()
            assert "Browser check failed" in captured.out
            assert "Browser crashed" in captured.out

    def test_check_browser_cleanup_on_executor_creation_failure(self, capsys):
        """Test check_browser handles executor creation failure gracefully."""
        with (
            patch("openhands.tools.preset.default.register_default_tools"),
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                side_effect=RuntimeError("Chromium not found"),
            ),
        ):
            from openhands.agent_server.__main__ import check_browser

            result = check_browser()

            assert result is False

            captured = capsys.readouterr()
            assert "Browser check failed" in captured.out
            assert "Chromium not found" in captured.out

    def test_check_browser_str_conversion_for_content(self, capsys):
        """Test that result.content is converted to string properly."""
        mock_result = MagicMock()
        mock_result.is_error = True
        # Use a non-string content to verify str() conversion
        mock_result.content = {"error": "complex error object"}

        mock_executor = MagicMock()
        mock_executor.return_value = mock_result

        with (
            patch("openhands.tools.preset.default.register_default_tools"),
            patch(
                "openhands.tools.browser_use.impl.BrowserToolExecutor",
                return_value=mock_executor,
            ),
        ):
            from openhands.agent_server.__main__ import check_browser

            result = check_browser()

            assert result is False

            captured = capsys.readouterr()
            assert "Browser check failed" in captured.out
            # The dict should be converted to string representation
            assert "error" in captured.out
