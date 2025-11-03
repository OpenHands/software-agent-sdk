"""Planning file editor tool - combines read-only viewing with PLAN.md editing."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from openhands.sdk.tool import (
    ToolAnnotations,
    ToolDefinition,
    register_tool,
)
from openhands.tools.file_editor.definition import (
    TOOL_DESCRIPTION as FILE_EDITOR_TOOL_DESCRIPTION,
    FileEditorAction,
    FileEditorObservation,
)


# Hardcoded plan filename
PLAN_FILENAME = "PLAN.md"


class PlanningFileEditorAction(FileEditorAction):
    """Schema for planning file editor operations.

    Inherits from FileEditorAction but restricts editing to PLAN.md only.
    Allows viewing any file but only editing PLAN.md.
    """


class PlanningFileEditorObservation(FileEditorObservation):
    """Observation from planning file editor operations.

    Inherits from FileEditorObservation - same structure, just different type.
    """


TOOL_DESCRIPTION = (
    FILE_EDITOR_TOOL_DESCRIPTION
    + """

IMPORTANT RESTRICTION FOR PLANNING AGENT:
* You can VIEW any file in the workspace using the 'view' command
* You can ONLY EDIT the PLAN.md file (all other edit operations will be rejected)
* PLAN.md is automatically initialized with section headers at the workspace root
* All editing commands (create, str_replace, insert, undo_edit) are restricted to PLAN.md only
* The PLAN.md file already contains the required section structure - you just need to fill in the content
"""  # noqa
)


class PlanningFileEditorTool(
    ToolDefinition[PlanningFileEditorAction, PlanningFileEditorObservation]
):
    """A planning file editor tool with read-all, edit-PLAN.md-only access."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState",
    ) -> Sequence["PlanningFileEditorTool"]:
        """Initialize PlanningFileEditorTool.

        Args:
            conv_state: Conversation state to get working directory from.
        """
        # Import here to avoid circular imports
        from openhands.tools.planning_file_editor.impl import (
            PlanningFileEditorExecutor,
        )

        working_dir = conv_state.workspace.working_dir
        workspace_root = Path(working_dir).resolve()
        plan_path = str(workspace_root / PLAN_FILENAME)

        # Initialize PLAN.md with headers if it doesn't exist
        plan_file = Path(plan_path)
        if not plan_file.exists():
            # Import here to avoid circular imports
            from openhands.tools.preset.planning import get_plan_headers

            plan_file.write_text(get_plan_headers())

        # Create executor with restricted edit access to PLAN.md only
        executor = PlanningFileEditorExecutor(
            workspace_root=working_dir,
            plan_path=plan_path,
        )

        # Add working directory information to the tool description
        enhanced_description = (
            f"{TOOL_DESCRIPTION}\n\n"
            f"Your current working directory: {working_dir}\n"
            f"Your PLAN.md location: {plan_path}\n"
            f"This plan file will be accessible to other agents in the workflow."
        )

        return [
            cls(
                description=enhanced_description,
                action_type=PlanningFileEditorAction,
                observation_type=PlanningFileEditorObservation,
                annotations=ToolAnnotations(
                    title="planning_file_editor",
                    readOnlyHint=False,  # Can edit PLAN.md
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


# Automatically register the tool when this module is imported
register_tool(PlanningFileEditorTool.name, PlanningFileEditorTool)
