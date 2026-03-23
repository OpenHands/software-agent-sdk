"""Tests for the SDK startup banner."""

import io
import re

import pytest

from openhands.sdk.banner import _print_banner


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


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


def _capture_banner(version: str = "1.0.0", force_color: bool = False) -> str:
    """Call _print_banner and capture its rich Console output."""
    from rich.console import Console

    import openhands.sdk.banner as banner_module

    buf = io.StringIO()
    console = Console(
        file=buf, stderr=True, force_terminal=force_color, no_color=(not force_color)
    )

    # Monkey-patch the Console constructor used inside _print_banner
    original_console = banner_module.Console
    banner_module.Console = lambda **_kwargs: console
    try:
        _print_banner(version)
    finally:
        banner_module.Console = original_console

    return buf.getvalue()


def test_banner_prints_content(reset_banner_state):
    """Test that the banner contains expected text."""
    output = _capture_banner("1.0.0")

    plain = _strip_ansi(output)
    assert "OpenHands SDK" in plain
    assert "v1.0.0" in plain
    assert "sdkbuilders.openhands.dev" in plain
    assert "LLM development credits" in plain
    assert "OpenHands Slack community" in plain
    assert "SDK docs, report bugs, and suggest features" in plain
    assert "OPENHANDS_SUPPRESS_BANNER=1" in plain


def test_banner_prints_only_once(reset_banner_state):
    """Test that the banner only prints once even if called multiple times."""
    output1 = _capture_banner("1.0.0")
    output2 = _capture_banner("1.0.0")
    output3 = _capture_banner("1.0.0")

    assert "OpenHands SDK" in _strip_ansi(output1)
    assert output2 == ""
    assert output3 == ""


def test_banner_with_color(reset_banner_state):
    """Test that the banner includes ANSI color codes when forced."""
    output = _capture_banner("1.0.0", force_color=True)

    assert "\033[" in output
    plain = _strip_ansi(output)
    assert "OpenHands SDK" in plain
    assert "sdkbuilders.openhands.dev" in plain


def test_banner_no_color(reset_banner_state):
    """Test that colors are off when not forced."""
    output = _capture_banner("1.0.0", force_color=False)

    assert "\033[" not in output
    assert "OpenHands SDK" in output


def test_banner_suppressed_by_env_var(monkeypatch, reset_banner_state):
    """Test that OPENHANDS_SUPPRESS_BANNER=1 suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "1")

    output = _capture_banner("1.0.0")
    assert output == ""


def test_banner_suppressed_by_env_var_true(monkeypatch, reset_banner_state):
    """Test that OPENHANDS_SUPPRESS_BANNER=true suppresses the banner."""
    monkeypatch.setenv("OPENHANDS_SUPPRESS_BANNER", "true")

    output = _capture_banner("1.0.0")
    assert output == ""
