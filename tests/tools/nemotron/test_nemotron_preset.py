"""Tests for Nemotron preset."""

from openhands.tools.nemotron import NEMOTRON_TOOLS, BashTool, StrReplaceTool
from openhands.tools.preset.nemotron import get_nemotron_tools


def test_nemotron_tools_list():
    """Test that NEMOTRON_TOOLS contains the expected tools."""
    tool_names = {t.name for t in NEMOTRON_TOOLS}
    assert "bash" in tool_names
    assert "str_replace" in tool_names


def test_get_nemotron_tools_returns_correct_tools():
    """Test that get_nemotron_tools returns the expected tools."""
    tools = get_nemotron_tools(enable_browser=False)
    tool_names = {t.name for t in tools}

    assert "bash" in tool_names
    assert "str_replace" in tool_names
    assert "task_tracker" in tool_names
    # Should not have browser tools when disabled
    assert "browser_use" not in tool_names


def test_bash_tool_name_is_bash():
    """Test that BashTool name is 'bash' (not 'terminal')."""
    assert BashTool.name == "bash"


def test_str_replace_tool_name_is_str_replace():
    """Test that StrReplaceTool name is 'str_replace' (not 'file_editor')."""
    assert StrReplaceTool.name == "str_replace"
