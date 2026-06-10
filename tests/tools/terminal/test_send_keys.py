"""Tests for standardized send_keys special key handling."""

import platform
import shutil
import tempfile
import time

import pytest

from openhands.tools.terminal.terminal.interface import (
    SUPPORTED_SPECIAL_KEYS,
    parse_ctrl_key,
)


# ── parse_ctrl_key ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("C-a", "C-a"),
        ("C-Z", "C-z"),
        ("CTRL-c", "C-c"),
        ("ctrl+d", "C-d"),
        ("CTRL+L", "C-l"),
        ("C-m", "C-m"),
    ],
)
def test_parse_ctrl_key_valid(text: str, expected: str) -> None:
    assert parse_ctrl_key(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "C-",
        "C-ab",
        "C-1",
        "hello",
        "CTRL-",
        "CTRL+12",
    ],
)
def test_parse_ctrl_key_invalid(text: str) -> None:
    assert parse_ctrl_key(text) is None


# ── SUPPORTED_SPECIAL_KEYS ──────────────────────────────────────────


def test_supported_special_keys_contains_essentials() -> None:
    for key in ("ENTER", "TAB", "ESC", "UP", "DOWN", "C-C", "C-D"):
        assert key in SUPPORTED_SPECIAL_KEYS


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="SubprocessTerminal is not available on Windows",
)
def test_subprocess_specials_match_contract() -> None:
    """Backend specials dicts must stay in sync with SUPPORTED_SPECIAL_KEYS."""
    from openhands.tools.terminal.terminal.subprocess_terminal import (
        _SUBPROCESS_SPECIALS,
    )

    assert set(_SUBPROCESS_SPECIALS.keys()) == SUPPORTED_SPECIAL_KEYS


def test_tmux_specials_match_contract() -> None:
    from openhands.tools.terminal.terminal.tmux_terminal import (
        _TMUX_SPECIALS,
    )

    assert set(_TMUX_SPECIALS.keys()) == SUPPORTED_SPECIAL_KEYS


# ── SubprocessTerminal.send_keys ────────────────────────────────────


@pytest.fixture
def subprocess_terminal():
    """Create a real SubprocessTerminal for send_keys testing."""
    if platform.system() == "Windows":
        pytest.skip("SubprocessTerminal not available on Windows")

    from openhands.tools.terminal.terminal.subprocess_terminal import (
        SubprocessTerminal,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        term = SubprocessTerminal(work_dir=tmpdir)
        term.initialize()
        yield term
        term.close()


def test_subprocess_send_keys_ctrl_c(subprocess_terminal) -> None:
    """C-c should be recognized as a special key (not sent as literal text)."""
    subprocess_terminal.send_keys("C-c")


def test_subprocess_send_keys_named_special(subprocess_terminal) -> None:
    """Named specials like TAB should be dispatched without error."""
    subprocess_terminal.send_keys("TAB")


def test_subprocess_send_keys_ctrl_variants(subprocess_terminal) -> None:
    """CTRL-x and CTRL+x forms should work."""
    subprocess_terminal.send_keys("CTRL-a")
    subprocess_terminal.send_keys("CTRL+e")


def test_subprocess_send_keys_echo(subprocess_terminal) -> None:
    """Verify data actually flows through the PTY dispatch path."""
    subprocess_terminal.send_keys("echo hello_subprocess")
    time.sleep(0.5)
    screen = subprocess_terminal.read_screen()
    assert "hello_subprocess" in screen


# ── TmuxTerminal.send_keys ─────────────────────────────────────────


@pytest.fixture
def tmux_terminal():
    """Create a real TmuxTerminal for send_keys testing."""
    if platform.system() == "Windows":
        pytest.skip("TmuxTerminal not available on Windows")
    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")

    from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal

    with tempfile.TemporaryDirectory() as tmpdir:
        term = TmuxTerminal(work_dir=tmpdir)
        term.initialize()
        yield term
        term.close()


def test_tmux_send_keys_ctrl_c(tmux_terminal) -> None:
    tmux_terminal.send_keys("C-c")


def test_tmux_send_keys_named_special(tmux_terminal) -> None:
    tmux_terminal.send_keys("TAB")
    tmux_terminal.send_keys("UP")
    tmux_terminal.send_keys("ESC")


def test_tmux_send_keys_ctrl_variants(tmux_terminal) -> None:
    tmux_terminal.send_keys("CTRL-a")
    tmux_terminal.send_keys("CTRL+e")


def test_tmux_send_keys_plain_text(tmux_terminal) -> None:
    """Plain text should be sent literally (not interpreted as a key name)."""
    tmux_terminal.send_keys("echo hello_world")
    time.sleep(0.3)
    screen = tmux_terminal.read_screen()
    assert "hello_world" in screen
