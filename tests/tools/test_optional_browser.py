"""Tests for optional browser-use dependency handling."""

import sys
from unittest.mock import patch

from openhands.tools.preset.default import (
    get_default_tools,
    register_default_tools,
)


def _hide_browser_use():
    """Remove browser_use from sys.modules so imports fail."""
    hidden = {}
    for k in list(sys.modules):
        if (
            k == "browser_use"
            or k.startswith("browser_use.")
            or k.startswith("openhands.tools.browser_use")
        ):
            hidden[k] = sys.modules.pop(k)
    return hidden


def test_register_default_tools_browser_disabled():
    """No browser tools registered when disabled."""
    register_default_tools(enable_browser=False)
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool

    assert FileEditorTool is not None
    assert TerminalTool is not None


def test_register_default_tools_browser_enabled():
    """Browser tools registered when enabled and installed."""
    register_default_tools(enable_browser=True)
    from openhands.tools.browser_use import BrowserToolSet

    assert BrowserToolSet is not None


def test_get_default_tools_without_browser():
    """Returns only non-browser tools when disabled."""
    tools = get_default_tools(enable_browser=False)
    names = {t.name for t in tools}
    assert len(tools) == 3
    assert "browser" not in names


def test_get_default_tools_with_browser():
    """Includes browser tool when enabled and installed."""
    tools = get_default_tools(enable_browser=True)
    names = {t.name for t in tools}
    assert len(tools) == 4
    assert "browser" in names


def test_get_default_tools_browser_missing(caplog):
    """Falls back to non-browser tools when import fails."""
    hidden = _hide_browser_use()
    try:
        with patch.dict(sys.modules, {"browser_use": None}):
            tools = get_default_tools(enable_browser=True)
        assert len(tools) == 3
        assert "browser-use is not installed" in caplog.text
    finally:
        sys.modules.update(hidden)
