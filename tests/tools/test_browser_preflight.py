"""Tests for the browser tool pre-flight check."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from openhands.tools.browser_use.impl import BrowserToolExecutor


def test_preflight_check_catches_missing_binary():
    """_ensure_chromium_available should raise if no binary is found."""
    mock_self = MagicMock(spec=BrowserToolExecutor)
    mock_self.check_chromium_available = MagicMock(return_value=None)
    with pytest.raises(Exception, match="chromium"):
        BrowserToolExecutor._ensure_chromium_available(mock_self)


def test_preflight_check_catches_binary_that_cant_launch():
    """_ensure_chromium_available should raise if the binary exists but
    can't launch (e.g. missing shared libraries)."""
    fake_path = "/usr/bin/chromium"
    mock_self = MagicMock(spec=BrowserToolExecutor)
    mock_self.check_chromium_available = MagicMock(return_value=fake_path)
    with patch("subprocess.run", side_effect=OSError("No such file or directory")):
        with pytest.raises(Exception, match="pre-flight check failed"):
            BrowserToolExecutor._ensure_chromium_available(mock_self)


def test_preflight_check_catches_binary_that_hangs():
    """_ensure_chromium_available should raise if the binary hangs on --version."""
    fake_path = "/usr/bin/chromium"
    mock_self = MagicMock(spec=BrowserToolExecutor)
    mock_self.check_chromium_available = MagicMock(return_value=fake_path)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=[fake_path, "--version"], timeout=10),
    ):
        with pytest.raises(Exception, match="did not respond"):
            BrowserToolExecutor._ensure_chromium_available(mock_self)


def test_preflight_check_catches_nonzero_exit():
    """_ensure_chromium_available should raise if --version exits nonzero."""
    fake_path = "/usr/bin/chromium"
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "error: missing libfoo.so"
    mock_self = MagicMock(spec=BrowserToolExecutor)
    mock_self.check_chromium_available = MagicMock(return_value=fake_path)
    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(Exception, match="exited with code 1"):
            BrowserToolExecutor._ensure_chromium_available(mock_self)


def test_preflight_check_passes_on_success():
    """_ensure_chromium_available should return the path if --version succeeds."""
    fake_path = "/usr/bin/chromium"
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "Chromium 120.0.0.0\n"
    mock_result.stderr = ""
    mock_self = MagicMock(spec=BrowserToolExecutor)
    mock_self.check_chromium_available = MagicMock(return_value=fake_path)
    with patch("subprocess.run", return_value=mock_result):
        result = BrowserToolExecutor._ensure_chromium_available(mock_self)
        assert result == fake_path
