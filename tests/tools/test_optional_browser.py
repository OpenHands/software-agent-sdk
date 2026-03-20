"""Tests for optional browser-use dependency handling."""

import logging
from unittest.mock import patch

from openhands.tools.preset.default import (
    get_default_tools,
    register_default_tools,
)


def test_register_default_tools_browser_disabled():
    """When enable_browser=False, no browser import is attempted."""
    register_default_tools(enable_browser=False)


def test_register_default_tools_browser_enabled():
    """When enable_browser=True and browser-use is installed, tools are registered."""
    register_default_tools(enable_browser=True)


def test_get_default_tools_without_browser():
    """When enable_browser=False, returns only non-browser tools."""
    tools = get_default_tools(enable_browser=False)
    assert len(tools) == 3  # terminal, file_editor, task_tracker


def test_get_default_tools_with_browser():
    """Browser tool included when browser-use is installed."""
    tools = get_default_tools(enable_browser=True)
    assert len(tools) == 4  # terminal, file_editor, task_tracker, browser


def test_get_default_tools_browser_missing_fallback(caplog):
    """Degrades gracefully when browser-use is missing."""
    original_import = __builtins__.__import__

    def mock_import(name, *args, **kwargs):
        if "browser_use" in name:
            raise ImportError("No module named 'browser_use'")
        return original_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        with caplog.at_level(logging.WARNING):
            tools = get_default_tools(enable_browser=True)

    # Should return only non-browser tools (no crash)
    assert len(tools) == 3
    assert "browser-use is not installed" in caplog.text
