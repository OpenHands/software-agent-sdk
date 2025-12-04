"""Tests for observability utils."""

import os
from unittest.mock import patch

from openhands.sdk.observability.utils import get_env


def test_get_env_from_environment():
    """Test that get_env returns value from environment variables."""
    with patch.dict(os.environ, {"TEST_VAR": "test_value"}):
        assert get_env("TEST_VAR") == "test_value"


def test_get_env_not_found():
    """Test that get_env returns None when variable is not found."""
    with patch.dict(os.environ, {}, clear=True):
        result = get_env("NONEXISTENT_VAR")
        assert result is None


def test_get_env_handles_dotenv_assertion_error():
    """Test that get_env handles AssertionError from find_dotenv gracefully."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("openhands.sdk.observability.utils.dotenv_values") as mock_dotenv:
            mock_dotenv.side_effect = AssertionError("Frame stack exhausted")
            result = get_env("TEST_VAR")
            assert result is None


def test_get_env_handles_dotenv_os_error():
    """Test that get_env handles OSError from dotenv gracefully."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("openhands.sdk.observability.utils.dotenv_values") as mock_dotenv:
            mock_dotenv.side_effect = OSError("File not found")
            result = get_env("TEST_VAR")
            assert result is None


def test_get_env_prefers_environment_over_dotenv():
    """Test that environment variables take precedence over dotenv."""
    with patch.dict(os.environ, {"TEST_VAR": "env_value"}):
        with patch("openhands.sdk.observability.utils.dotenv_values") as mock_dotenv:
            mock_dotenv.return_value = {"TEST_VAR": "dotenv_value"}
            result = get_env("TEST_VAR")
            assert result == "env_value"
            mock_dotenv.assert_not_called()


def test_get_env_from_dotenv():
    """Test that get_env can retrieve values from dotenv file."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("openhands.sdk.observability.utils.dotenv_values") as mock_dotenv:
            mock_dotenv.return_value = {"TEST_VAR": "dotenv_value"}
            result = get_env("TEST_VAR")
            assert result == "dotenv_value"
