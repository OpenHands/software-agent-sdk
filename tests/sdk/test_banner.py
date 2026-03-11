"""Tests for the SDK startup banner."""

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


def test_banner_prints_to_stderr(reset_banner_state, capsys):
    """Test that the banner prints to stderr."""
    _print_banner("1.0.0")

    captured = capsys.readouterr()
    assert "OpenHands SDK v1.0.0" in captured.err
    assert "github.com/OpenHands/software-agent-sdk/issues" in captured.err
    assert "openhands.dev/joinslack" in captured.err
    assert "openhands.dev/product/sdk" in captured.err
    assert "OPENHANDS_SUPPRESS_BANNER=1" in captured.err
    assert captured.out == ""


def test_banner_prints_only_once(reset_banner_state, capsys):
    """Test that the banner only prints once even if called multiple times."""
    _print_banner("1.0.0")
    _print_banner("1.0.0")
    _print_banner("1.0.0")

    captured = capsys.readouterr()
    assert captured.err.count("OpenHands SDK") == 1


def test_banner_suppressed_by_env_var(monkeypatch, reset_banner_state, capsys):
    """Test that OPENHANDS_SUPPRESS_BANNER=1 suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "1")

    _print_banner("1.0.0")

    captured = capsys.readouterr()
    assert captured.err == ""


def test_banner_suppressed_by_env_var_true(monkeypatch, reset_banner_state, capsys):
    """Test that OPENHANDS_SUPPRESS_BANNER=true suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "true")

    _print_banner("1.0.0")

    captured = capsys.readouterr()
    assert captured.err == ""
