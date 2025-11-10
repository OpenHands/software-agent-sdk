"""Test that tool_name class variables are consistent with automatic naming."""

from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.glob import GlobTool
from openhands.tools.grep import GrepTool
from openhands.tools.planning_file_editor import PlanningFileEditorTool
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool


def test_tool_name_attributes_exist():
    """Test that all tool classes have name class variables."""
    tools = [
        TerminalTool,
        FileEditorTool,
        TaskTrackerTool,
        BrowserToolSet,
        GrepTool,
        GlobTool,
        PlanningFileEditorTool,
    ]

    for tool_class in tools:
        assert hasattr(tool_class, "name"), (
            f"{tool_class.__name__} missing name attribute"
        )
        assert isinstance(tool_class.name, str), (
            f"{tool_class.__name__}.name is not a string"
        )
        # name should be snake_case version of class name
        assert tool_class.name.islower(), (
            f"{tool_class.__name__}.name should be snake_case"
        )
        # Allow single words without underscores (e.g., "terminal", "grep")
        assert "_" in tool_class.name or len(tool_class.name) <= 10, (
            f"{tool_class.__name__}.name should contain underscores for "
            "multi-word names or be a short single word"
        )


def test_tool_name_consistency():
    """Test that name matches the expected snake_case conversion."""
    expected_names = {
        TerminalTool: "terminal",
        FileEditorTool: "file_editor",
        TaskTrackerTool: "task_tracker",
        BrowserToolSet: "browser_tool_set",
        GrepTool: "grep",
        GlobTool: "glob",
        PlanningFileEditorTool: "planning_file_editor",
    }

    for tool_class, expected_name in expected_names.items():
        assert tool_class.name == expected_name, (
            f"{tool_class.__name__}.name should be '{expected_name}'"
        )


def test_tool_name_accessible_at_class_level():
    """Test that name can be accessed at the class level without instantiation."""
    # This should not raise any errors and should return snake_case names
    assert TerminalTool.name == "terminal"
    assert FileEditorTool.name == "file_editor"
    assert TaskTrackerTool.name == "task_tracker"
    assert BrowserToolSet.name == "browser_tool_set"
    assert GrepTool.name == "grep"
    assert GlobTool.name == "glob"
    assert PlanningFileEditorTool.name == "planning_file_editor"
