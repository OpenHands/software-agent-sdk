"""Tests for VNC integration with browser tool executor."""

import os
from unittest.mock import patch

import pytest

from openhands.tools.browser_use.impl import get_browser_executor_class


BrowserToolExecutor = get_browser_executor_class()


class TestVNCIntegration:
    """Test VNC integration with browser tool executor."""

    def test_vnc_disabled_headless_mode_preserved(self):
        """Test that headless mode is preserved when VNC is disabled."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": "false"}, clear=False):
            executor = BrowserToolExecutor(headless=True)
            assert executor._config["headless"] is True

    def test_vnc_disabled_non_headless_mode_preserved(self):
        """Test that non-headless mode is preserved when VNC is disabled."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": "false"}, clear=False):
            executor = BrowserToolExecutor(headless=False)
            assert executor._config["headless"] is False

    def test_vnc_enabled_forces_non_headless_mode_from_true(self):
        """Test that VNC enabled forces non-headless mode from headless=True."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": "true"}, clear=False):
            executor = BrowserToolExecutor(headless=True)
            assert executor._config["headless"] is False

    def test_vnc_enabled_preserves_non_headless_mode_from_false(self):
        """Test that VNC enabled preserves non-headless mode from headless=False."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": "true"}, clear=False):
            executor = BrowserToolExecutor(headless=False)
            assert executor._config["headless"] is False

    @pytest.mark.parametrize(
        "env_value", ["true", "True", "TRUE", "1", "yes", "Yes", "YES"]
    )
    def test_vnc_enabled_various_true_values(self, env_value):
        """Test that various truthy values for OH_ENABLE_VNC work correctly."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": env_value}, clear=False):
            executor = BrowserToolExecutor(headless=True)
            assert executor._config["headless"] is False

    @pytest.mark.parametrize(
        "env_value", ["false", "False", "FALSE", "0", "no", "No", "NO", ""]
    )
    def test_vnc_disabled_various_false_values(self, env_value):
        """Test that various falsy values for OH_ENABLE_VNC work correctly."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": env_value}, clear=False):
            executor = BrowserToolExecutor(headless=True)
            assert executor._config["headless"] is True

    def test_vnc_not_set_defaults_to_disabled(self):
        """Test that when OH_ENABLE_VNC is not set, it defaults to disabled."""
        # Remove OH_ENABLE_VNC from environment if it exists
        env_copy = os.environ.copy()
        if "OH_ENABLE_VNC" in env_copy:
            del env_copy["OH_ENABLE_VNC"]

        with patch.dict(os.environ, env_copy, clear=True):
            executor = BrowserToolExecutor(headless=True)
            assert executor._config["headless"] is True

    def test_vnc_enabled_logs_message(self):
        """Test that VNC enabled logs appropriate message by mocking logger."""
        with (
            patch.dict(os.environ, {"OH_ENABLE_VNC": "true"}, clear=False),
            patch("openhands.tools.browser_use.impl.logger") as mock_logger,
        ):
            BrowserToolExecutor(headless=True)
            mock_logger.info.assert_called_with(
                "VNC is enabled - running browser in non-headless mode"
            )

    def test_vnc_disabled_no_log_message(self):
        """Test that VNC disabled doesn't log VNC-specific messages."""
        with (
            patch.dict(os.environ, {"OH_ENABLE_VNC": "false"}, clear=False),
            patch("openhands.tools.browser_use.impl.logger") as mock_logger,
        ):
            BrowserToolExecutor(headless=True)
            # Verify that the VNC-specific log message was not called
            vnc_calls = [
                call
                for call in mock_logger.info.call_args_list
                if "VNC is enabled" in str(call)
            ]
            assert len(vnc_calls) == 0

    def test_vnc_config_with_other_parameters(self):
        """Test VNC configuration works with other browser parameters."""
        with patch.dict(os.environ, {"OH_ENABLE_VNC": "true"}, clear=False):
            executor = BrowserToolExecutor(
                headless=True,
                allowed_domains=["example.com"],
                session_timeout_minutes=60,
                custom_param="test_value",
            )

            assert executor._config["headless"] is False
            assert executor._config["allowed_domains"] == ["example.com"]
            assert executor._config["custom_param"] == "test_value"

    def test_vnc_environment_variable_case_insensitive(self):
        """Test that OH_ENABLE_VNC environment variable is case insensitive."""
        test_cases = [
            ("True", False),
            ("TRUE", False),
            ("true", False),
            ("1", False),
            ("yes", False),
            ("YES", False),
            ("False", True),
            ("FALSE", True),
            ("false", True),
            ("0", True),
            ("no", True),
            ("NO", True),
        ]

        for env_value, expected_headless in test_cases:
            with patch.dict(os.environ, {"OH_ENABLE_VNC": env_value}, clear=False):
                executor = BrowserToolExecutor(headless=True)
                assert executor._config["headless"] is expected_headless, (
                    f"Failed for OH_ENABLE_VNC={env_value}"
                )
