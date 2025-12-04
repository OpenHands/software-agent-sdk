"""Tests for laminar observability functions."""

import os
from unittest.mock import patch

from openhands.sdk.observability.laminar import should_enable_observability


def test_should_enable_observability_with_dotenv_error():
    """Test that should_enable_observability handles dotenv errors gracefully."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("openhands.sdk.observability.utils.dotenv_values") as mock_dotenv:
            mock_dotenv.side_effect = AssertionError("Frame stack exhausted")
            with patch(
                "openhands.sdk.observability.laminar.Laminar.is_initialized"
            ) as mock_init:
                mock_init.return_value = False
                result = should_enable_observability()
                assert result is False


def test_should_enable_observability_returns_true_when_env_set():
    """Test that should_enable_observability returns True when env vars are set."""
    with patch.dict(os.environ, {"LMNR_PROJECT_API_KEY": "test_key"}):
        with patch(
            "openhands.sdk.observability.laminar.Laminar.is_initialized"
        ) as mock_init:
            mock_init.return_value = False
            result = should_enable_observability()
            assert result is True


def test_should_enable_observability_returns_false_when_no_env():
    """Test that should_enable_observability returns False when no env vars set."""
    with patch.dict(os.environ, {}, clear=True):
        with patch(
            "openhands.sdk.observability.laminar.Laminar.is_initialized"
        ) as mock_init:
            mock_init.return_value = False
            result = should_enable_observability()
            assert result is False
