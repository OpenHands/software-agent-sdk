"""Tests for get_planning_tools() plan_path parameter forwarding."""

from openhands.tools.planning_file_editor import PlanningFileEditorTool
from openhands.tools.preset.planning import get_planning_tools


def test_get_planning_tools_without_plan_path_has_empty_params():
    """When plan_path is not provided, PlanningFileEditorTool spec has empty params."""
    # Act
    tools = get_planning_tools()

    # Assert
    planning_tool = next(t for t in tools if t.name == PlanningFileEditorTool.name)
    assert planning_tool.params == {}


def test_get_planning_tools_with_plan_path_passes_params():
    """When plan_path is provided, it is passed in PlanningFileEditorTool params."""
    # Arrange
    expected_path = "/workspace/project/.openhands/PLAN.md"

    # Act
    tools = get_planning_tools(plan_path=expected_path)

    # Assert
    planning_tool = next(t for t in tools if t.name == PlanningFileEditorTool.name)
    assert planning_tool.params == {"plan_path": expected_path}
