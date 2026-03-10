"""Tests for the SDK startup banner."""

import io
import sys

import pytest

from openhands.sdk.banner import _print_banner


@pytest.fixture
def reset_banner_state(monkeypatch):
    """Reset the banner state and env var before and after each test."""
    import openhands.sdk.banner as banner_module

    # Remove suppress env var if set (e.g., from CI)
    monkeypatch.delenv("OPENHANDS_SUPPRESS_BANNER", raising=False)

    original_state = banner_module._BANNER_PRINTED
    banner_module._BANNER_PRINTED = False
    yield
    banner_module._BANNER_PRINTED = original_state


def test_banner_prints_to_stderr(reset_banner_state):
    """Test that the banner prints to stderr."""
    captured = io.StringIO()
    original_stderr = sys.stderr
    sys.stderr = captured
    try:
        _print_banner("1.0.0")
    finally:
        sys.stderr = original_stderr

    output = captured.getvalue()
    assert "OpenHands SDK v1.0.0" in output
    assert "github.com/OpenHands/software-agent-sdk/issues" in output
    assert "openhands.dev/joinslack" in output
    assert "openhands.dev/product/sdk" in output
    assert "OPENHANDS_SUPPRESS_BANNER=1" in output


def test_banner_prints_only_once(reset_banner_state):
    """Test that the banner only prints once even if called multiple times."""
    captured = io.StringIO()
    original_stderr = sys.stderr
    sys.stderr = captured
    try:
        _print_banner("1.0.0")
        _print_banner("1.0.0")
        _print_banner("1.0.0")
    finally:
        sys.stderr = original_stderr

    output = captured.getvalue()
    # Should only appear once
    assert output.count("OpenHands SDK") == 1


def test_banner_suppressed_by_env_var(monkeypatch, reset_banner_state):
    """Test that OPENHANDS_SUPPRESS_BANNER=1 suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "1")

    captured = io.StringIO()
    original_stderr = sys.stderr
    sys.stderr = captured
    try:
        _print_banner("1.0.0")
    finally:
        sys.stderr = original_stderr

    output = captured.getvalue()
    assert output == ""


def test_banner_suppressed_by_env_var_true(monkeypatch, reset_banner_state):
    """Test that OPENHANDS_SUPPRESS_BANNER=true suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "true")

    captured = io.StringIO()
    original_stderr = sys.stderr
    sys.stderr = captured
    try:
        _print_banner("1.0.0")
    finally:
        sys.stderr = original_stderr

    output = captured.getvalue()
    assert output == ""
