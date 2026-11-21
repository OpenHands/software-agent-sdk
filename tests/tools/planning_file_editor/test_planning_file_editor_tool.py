"""Tests for planning file editor setup and execution."""

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.planning_file_editor import PlanningFileEditorTool
from openhands.tools.planning_file_editor.definition import PlanningFileEditorAction
from openhands.tools.planning_file_editor.impl import PlanningFileEditorExecutor


def _create_conv_state(working_dir: str) -> ConversationState:
    """Create a minimal conversation state for tests."""
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm")
    agent = Agent(llm=llm, tools=[])
    return ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=working_dir),
    )


def test_create_without_plan_path_uses_agents_tmp_directory():
    """When plan_path is not provided, PLAN.md is created in .agents_tmp at workspace
    root."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        conv_state = _create_conv_state(temp_dir)
        expected_path = Path(temp_dir).resolve() / ".agents_tmp" / "PLAN.md"

        # Act
        tools = PlanningFileEditorTool.create(conv_state)
        tool = tools[0]

        # Assert
        assert len(tools) == 1
        assert tool.executor is not None
        assert issubclass(tool.action_type, PlanningFileEditorAction)
        assert expected_path.exists()
        assert str(expected_path) in tool.description


def test_create_with_plan_path_uses_given_path():
    """When plan_path is provided, PLAN.md is created at that path."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        conv_state = _create_conv_state(temp_dir)
        custom_path = str(Path(temp_dir) / ".agents_tmp" / "PLAN.md")

        # Act
        tools = PlanningFileEditorTool.create(conv_state, plan_path=custom_path)
        tool = tools[0]

        # Assert
        assert Path(custom_path).exists()
        assert custom_path in tool.description


def test_create_with_plan_path_creates_parent_directory():
    """When plan_path is in a non-existent subdir, parent directory is created."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        conv_state = _create_conv_state(temp_dir)
        custom_path = str(Path(temp_dir) / "config" / "nested" / "PLAN.md")
        assert not Path(custom_path).parent.exists()

        # Act
        PlanningFileEditorTool.create(conv_state, plan_path=custom_path)

        # Assert
        assert Path(custom_path).parent.exists()
        assert Path(custom_path).exists()


def test_create_without_plan_path_uses_legacy_location_if_exists():
    """When legacy PLAN.md exists at workspace root, it is used for backward compatibility."""  # noqa: E501
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        conv_state = _create_conv_state(temp_dir)
        legacy_path = Path(temp_dir).resolve() / "PLAN.md"
        new_path = Path(temp_dir).resolve() / ".agents_tmp" / "PLAN.md"

        # Create a legacy PLAN.md at workspace root
        legacy_path.write_text("# Legacy Plan Content")

        # Act
        tools = PlanningFileEditorTool.create(conv_state)
        tool = tools[0]

        # Assert - tool uses legacy path
        assert str(legacy_path) in tool.description
        assert legacy_path.exists()
        # New location should not be created
        assert not new_path.exists()


def test_create_with_relative_path_raises_value_error():
    """When plan_path is relative, ValueError is raised."""
    with tempfile.TemporaryDirectory() as temp_dir:
        # Arrange
        conv_state = _create_conv_state(temp_dir)
        relative_path = "relative/path/PLAN.md"

        # Act & Assert
        with pytest.raises(
            ValueError, match="plan_path must be an absolute path, got: relative"
        ):
            PlanningFileEditorTool.create(conv_state, plan_path=relative_path)


def test_create_observation_preserves_new_file_state(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    editor = PlanningFileEditorExecutor(
        workspace_root=str(tmp_path), plan_path=str(plan)
    )

    observation = editor(
        PlanningFileEditorAction(command="create", path=str(plan), file_text="# Plan\n")
    )

    assert plan.read_text() == "# Plan\n"
    assert not observation.is_error
    assert observation.prev_exist is False
    assert observation.new_content == "# Plan\n"


def test_edit_observation_preserves_the_rendered_diff(tmp_path: Path):
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Plan\nWrite first draft\n")
    editor = PlanningFileEditorExecutor(
        workspace_root=str(tmp_path), plan_path=str(plan)
    )

    observation = editor(
        PlanningFileEditorAction(
            command="str_replace",
            path=str(plan),
            old_str="Write first draft",
            new_str="Review first draft",
        )
    )

    assert not observation.is_error
    assert observation.prev_exist is True
    assert observation.old_content == "# Plan\nWrite first draft\n"
    assert observation.new_content == plan.read_text() == "# Plan\nReview first draft\n"
    assert "-2|Write first draft" in observation.visualize.plain
    assert "+2|Review first draft" in observation.visualize.plain
    assert observation.model_dump()["kind"] == "PlanningFileEditorObservation"
