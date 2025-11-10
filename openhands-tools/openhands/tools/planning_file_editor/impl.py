"""Implementation of the planning file editor tool."""

from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.file_editor.impl import FileEditorExecutor
from openhands.tools.planning_file_editor.definition import (
    PlanningFileEditorAction,
    PlanningFileEditorObservation,
)


class PlanningFileEditorExecutor(ToolExecutor):
    """Executor for planning file editor that wraps FileEditorExecutor."""

    def __init__(self, workspace_root: str, plan_path: str):
        """Initialize the executor.

        Args:
            workspace_root: Root directory for file operations
            plan_path: Absolute path to PLAN.md file
        """
        self.file_editor_executor: FileEditorExecutor = FileEditorExecutor(
            workspace_root=workspace_root,
            allowed_edits_files=[plan_path],
        )

    def __call__(
        self,
        action: PlanningFileEditorAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> PlanningFileEditorObservation:
        """Execute the planning file editor action.

        Args:
            action: The planning file editor action to execute

        Returns:
            PlanningFileEditorObservation with the result
        """
        # Convert PlanningFileEditorAction to FileEditorAction
        file_editor_action = FileEditorAction(
            command=action.command,
            path=action.path,
            file_text=action.file_text,
            old_str=action.old_str,
            new_str=action.new_str,
            insert_line=action.insert_line,
            view_range=action.view_range,
        )

        # Execute with FileEditorExecutor
        file_editor_obs = self.file_editor_executor(file_editor_action)

        # Convert FileEditorObservation to PlanningFileEditorObservation
        return PlanningFileEditorObservation(
            command=action.command,
            content=file_editor_obs.content,
            is_error=file_editor_obs.is_error,
            path=file_editor_obs.path,
        )
