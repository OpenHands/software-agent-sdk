"""Test automatic tool registration functionality."""

from openhands.sdk.tool.registry import list_registered_tools


def test_bash_tool_automatic_registration():
    """Test that TerminalTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.terminal.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "terminal" in registered_tools


def test_file_editor_tool_automatic_registration():
    """Test that FileEditorTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.file_editor.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "file_editor" in registered_tools


def test_task_tracker_tool_automatic_registration():
    """Test that TaskTrackerTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.task_tracker.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "task_tracker" in registered_tools


def test_browser_tool_automatic_registration():
    """Test that BrowserToolSet is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.browser_use.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "browser_tool_set" in registered_tools


def test_grep_tool_automatic_registration():
    """Test that GrepTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.grep.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "grep" in registered_tools


def test_glob_tool_automatic_registration():
    """Test that GlobTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.glob.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "glob" in registered_tools


def test_planning_file_editor_tool_automatic_registration():
    """Test that PlanningFileEditorTool is automatically registered when imported."""
    # Import the module to trigger registration
    import openhands.tools.planning_file_editor.definition  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "planning_file_editor" in registered_tools


def test_import_from_init_triggers_registration():
    """Test that importing from __init__.py also triggers registration."""
    # Import from the __init__.py file
    from openhands.tools.terminal import TerminalTool  # noqa: F401

    # Check that the tool is registered with snake_case name
    registered_tools = list_registered_tools()
    assert "terminal" in registered_tools


def test_tool_can_be_resolved_after_automatic_registration():
    """Test that automatically registered tools can be resolved and used."""
    from unittest.mock import MagicMock

    # Import to trigger registration
    import openhands.tools.terminal.definition  # noqa: F401
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.tool.registry import resolve_tool
    from openhands.sdk.tool.spec import Tool

    # Create a mock conversation state
    mock_conv_state = MagicMock(spec=ConversationState)
    mock_workspace = MagicMock()
    mock_workspace.working_dir = "/tmp"
    mock_conv_state.workspace = mock_workspace

    # Try to resolve the tool using snake_case name
    tool_spec = Tool(name="terminal")
    resolved_tools = resolve_tool(tool_spec, mock_conv_state)

    # Should successfully resolve
    assert len(resolved_tools) == 1
    assert resolved_tools[0].name == "terminal"
