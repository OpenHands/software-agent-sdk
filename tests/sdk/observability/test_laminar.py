"""Tests for Laminar observability configuration."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("https://custom.lmnr.ai", "https://custom.lmnr.ai"),
        ("http://localhost:8080", "http://localhost:8080"),
        ("", None),
        (None, None),
    ],
)
def test_lmnr_base_url_parsing(env_value, expected):
    """Test that LMNR_BASE_URL is correctly parsed and passed to Laminar."""
    import os

    # Save original value
    original = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        # Set up environment
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if env_value is not None:
            os.environ["LMNR_BASE_URL"] = env_value
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]

        from openhands.sdk.observability.laminar import get_env

        result = get_env("LMNR_BASE_URL")
        if expected is None:
            assert result is None or result == ""
        else:
            assert result == expected
    finally:
        # Restore original values
        if original is not None:
            os.environ["LMNR_BASE_URL"] = original
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]


def test_lmnr_base_url_passed_to_laminar():
    """Test that LMNR_BASE_URL is correctly passed to Laminar.initialize."""
    import os

    # Save original values
    original_base_url = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        os.environ["LMNR_BASE_URL"] = "https://custom.lmnr.ai"

        with patch("openhands.sdk.observability.laminar.Laminar") as mock_laminar:
            with patch("openhands.sdk.observability.laminar.LaminarLiteLLMCallback"):
                with patch(
                    "openhands.sdk.observability.laminar.litellm"
                ) as mock_litellm:
                    mock_laminar.is_initialized.return_value = False
                    mock_litellm.callbacks = MagicMock()
                    from openhands.sdk.observability.laminar import maybe_init_laminar

                    maybe_init_laminar()

                    # Check that Laminar.initialize was called with base_url
                    call_kwargs = mock_laminar.initialize.call_args.kwargs
                    assert call_kwargs.get("base_url") == "https://custom.lmnr.ai"
    finally:
        # Restore original values
        if original_base_url is not None:
            os.environ["LMNR_BASE_URL"] = original_base_url
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]


def test_lmnr_base_url_not_passed_when_empty():
    """Test that base_url is None when LMNR_BASE_URL is not set."""
    import os

    # Save original values
    original_base_url = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]

        with patch("openhands.sdk.observability.laminar.Laminar") as mock_laminar:
            with patch("openhands.sdk.observability.laminar.LaminarLiteLLMCallback"):
                with patch(
                    "openhands.sdk.observability.laminar.litellm"
                ) as mock_litellm:
                    mock_laminar.is_initialized.return_value = False
                    mock_litellm.callbacks = MagicMock()
                    from openhands.sdk.observability.laminar import maybe_init_laminar

                    maybe_init_laminar()

                    # Check that Laminar.initialize was called with base_url=None
                    call_kwargs = mock_laminar.initialize.call_args.kwargs
                    assert call_kwargs.get("base_url") is None
    finally:
        # Restore original values
        if original_base_url is not None:
            os.environ["LMNR_BASE_URL"] = original_base_url
        elif "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]
