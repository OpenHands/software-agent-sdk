"""Implementation of the planning file editor tool."""

from typing import TYPE_CHECKING

from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
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
        # PlanningFileEditorAction is a FileEditorAction, so the base executor
        # takes it unchanged. Rebuilding one field by field only lets the two
        # definitions drift apart.
        file_editor_obs = self.file_editor_executor(action)

        # Carry the whole base observation across rather than a hand-maintained
        # field list, which silently dropped prev_exist / old_content /
        # new_content and would drop any field added to the base later. `kind`
        # is a computed discriminator and is re-derived by this subclass.
        return PlanningFileEditorObservation(
            **file_editor_obs.model_dump(exclude={"kind"})
        )
