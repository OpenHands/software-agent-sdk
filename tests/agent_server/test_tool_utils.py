"""Tests for tool_utils.py dynamic tool registration."""

from openhands.agent_server.tool_utils import (
    TOOL_MODULE_MAP,
    register_tools_by_name,
)
from openhands.sdk.tool.registry import list_registered_tools


def test_register_tools_by_name_glob():
    """Test that glob tool can be registered dynamically."""
    # Register the glob tool
    register_tools_by_name(["glob"])

    # Verify it's in the registry
    registered_tools = list_registered_tools()
    assert "glob" in registered_tools


def test_register_tools_by_name_grep():
    """Test that grep tool can be registered dynamically."""
    # Register the grep tool
    register_tools_by_name(["grep"])

    # Verify it's in the registry
    registered_tools = list_registered_tools()
    assert "grep" in registered_tools


def test_register_tools_by_name_planning_file_editor():
    """Test that planning_file_editor tool can be registered dynamically."""
    # Register the planning_file_editor tool
    register_tools_by_name(["planning_file_editor"])

    # Verify it's in the registry
    registered_tools = list_registered_tools()
    assert "planning_file_editor" in registered_tools


def test_register_tools_by_name_multiple():
    """Test that multiple tools can be registered at once."""
    # Register multiple tools
    tools_to_register = ["glob", "grep", "planning_file_editor"]
    register_tools_by_name(tools_to_register)

    # Verify all are in the registry
    registered_tools = list_registered_tools()
    for tool_name in tools_to_register:
        assert tool_name in registered_tools


def test_register_tools_by_name_unknown_tool():
    """Test that unknown tool names are handled gracefully."""
    # This should log a warning but not raise an exception
    register_tools_by_name(["unknown_tool"])

    # Verify it's not in the registry
    registered_tools = list_registered_tools()
    assert "unknown_tool" not in registered_tools


def test_tool_module_map_contains_planning_tools():
    """Test that TOOL_MODULE_MAP contains all planning tools."""
    assert "glob" in TOOL_MODULE_MAP
    assert "grep" in TOOL_MODULE_MAP
    assert "planning_file_editor" in TOOL_MODULE_MAP


def test_tool_module_map_contains_default_tools():
    """Test that TOOL_MODULE_MAP contains default tools."""
    assert "terminal" in TOOL_MODULE_MAP
    assert "file_editor" in TOOL_MODULE_MAP
    assert "task_tracker" in TOOL_MODULE_MAP
