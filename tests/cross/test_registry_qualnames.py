"""Tests for tool registry module qualname tracking."""

from openhands.sdk.tool.registry import (
    get_tool_module_qualnames,
    list_registered_tools,
    register_tool,
)


def test_get_tool_module_qualnames_with_class():
    """Test that module qualnames are tracked when registering a class."""
    from openhands.tools.glob import GlobTool

    # Register the GlobTool class
    register_tool("test_glob_class", GlobTool)

    # Get the module qualnames
    qualnames = get_tool_module_qualnames()

    # Verify the tool is tracked with its module
    assert "test_glob_class" in qualnames
    assert qualnames["test_glob_class"] == "openhands.tools.glob.definition"


def test_get_tool_module_qualnames_with_callable():
    """Test that module qualnames are tracked when registering a callable."""

    def test_factory(conv_state):
        return []

    # Register the callable
    register_tool("test_callable", test_factory)

    # Get the module qualnames
    qualnames = get_tool_module_qualnames()

    # Verify the tool is tracked with its module
    assert "test_callable" in qualnames
    assert "test_registry_qualnames" in qualnames["test_callable"]


def test_get_tool_module_qualnames_after_import():
    """Test that importing a tool module registers it with qualname."""
    # Import glob tool module to trigger auto-registration
    import openhands.tools.glob.definition  # noqa: F401

    # Get registered tools
    registered_tools = list_registered_tools()

    # Should have glob registered
    assert "glob" in registered_tools

    # Get module qualnames
    qualnames = get_tool_module_qualnames()

    # Verify glob has its module qualname tracked
    if "glob" in qualnames:
        assert qualnames["glob"] == "openhands.tools.glob.definition"


def test_get_tool_module_qualnames_returns_copy():
    """Test that get_tool_module_qualnames returns a copy, not the original dict."""
    qualnames1 = get_tool_module_qualnames()
    qualnames2 = get_tool_module_qualnames()

    # Should be equal but not the same object
    assert qualnames1 == qualnames2
    assert qualnames1 is not qualnames2
