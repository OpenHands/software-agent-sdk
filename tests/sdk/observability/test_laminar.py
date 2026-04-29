"""Tests for Laminar observability configuration."""

import os
from unittest.mock import patch

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

        with patch("lmnr.Laminar") as mock_laminar:
            mock_laminar.is_initialized.return_value = False
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
    # Save original values
    original_base_url = os.environ.get("LMNR_BASE_URL")
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if "LMNR_BASE_URL" in os.environ:
            del os.environ["LMNR_BASE_URL"]

        with patch("lmnr.Laminar") as mock_laminar:
            mock_laminar.is_initialized.return_value = False
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


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("on", True),
        ("ON", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
        (None, False),
    ],
)
def test_get_bool_env(env_value, expected):
    """Test that _get_bool_env correctly parses boolean environment variables."""
    original = os.environ.get("TEST_BOOL_VAR")

    try:
        if env_value is not None:
            os.environ["TEST_BOOL_VAR"] = env_value
        elif "TEST_BOOL_VAR" in os.environ:
            del os.environ["TEST_BOOL_VAR"]

        from openhands.sdk.observability.laminar import _get_bool_env

        result = _get_bool_env("TEST_BOOL_VAR")
        assert result == expected
    finally:
        if original is not None:
            os.environ["TEST_BOOL_VAR"] = original
        elif "TEST_BOOL_VAR" in os.environ:
            del os.environ["TEST_BOOL_VAR"]


@pytest.mark.parametrize(
    ("force_http_value", "expected_force_http"),
    [
        ("true", True),
        ("1", True),
        ("false", False),
        ("0", False),
        (None, False),
    ],
)
def test_lmnr_force_http_passed_to_laminar(force_http_value, expected_force_http):
    """Test that LMNR_FORCE_HTTP is correctly passed to Laminar.initialize."""
    original_key = os.environ.get("LMNR_PROJECT_API_KEY")
    original_force_http = os.environ.get("LMNR_FORCE_HTTP")

    try:
        os.environ["LMNR_PROJECT_API_KEY"] = "test-key"
        if force_http_value is not None:
            os.environ["LMNR_FORCE_HTTP"] = force_http_value
        elif "LMNR_FORCE_HTTP" in os.environ:
            del os.environ["LMNR_FORCE_HTTP"]

        with patch("lmnr.Laminar") as mock_laminar:
            mock_laminar.is_initialized.return_value = False
            from openhands.sdk.observability.laminar import maybe_init_laminar

            maybe_init_laminar()

            call_kwargs = mock_laminar.initialize.call_args.kwargs
            assert call_kwargs.get("force_http") == expected_force_http
    finally:
        if original_key is not None:
            os.environ["LMNR_PROJECT_API_KEY"] = original_key
        elif "LMNR_PROJECT_API_KEY" in os.environ:
            del os.environ["LMNR_PROJECT_API_KEY"]
        if original_force_http is not None:
            os.environ["LMNR_FORCE_HTTP"] = original_force_http
        elif "LMNR_FORCE_HTTP" in os.environ:
            del os.environ["LMNR_FORCE_HTTP"]
