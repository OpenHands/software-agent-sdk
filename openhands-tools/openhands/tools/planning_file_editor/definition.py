"""Planning file editor tool - combines read-only viewing with PLAN.md editing."""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from openhands.sdk.logger import get_logger
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


logger = get_logger(__name__)

# Default config directory and plan filename
# PLAN.md is now stored in .agents_tmp/ to keep workspace root clean
# and separate agent temporary files from user content
DEFAULT_CONFIG_DIR = ".agents_tmp"
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
        plan_path: str | None = None,
    ) -> Sequence["PlanningFileEditorTool"]:
        """Initialize PlanningFileEditorTool.

        Args:
            conv_state: Conversation state to get working directory from.
            plan_path: Optional absolute path to PLAN.md file. If not provided,
                defaults to {working_dir}/.agents_tmp/PLAN.md.

        Raises:
            ValueError: If plan_path is provided but is not an absolute path.
        """
        # Import here to avoid circular imports
        from openhands.tools.planning_file_editor.impl import (
            PlanningFileEditorExecutor,
        )

        working_dir = conv_state.workspace.working_dir

        # Validate plan_path is absolute if provided
        if plan_path is not None and not Path(plan_path).is_absolute():
            raise ValueError(f"plan_path must be an absolute path, got: {plan_path}")

        # Use provided plan_path or fall back to .agents_tmp/PLAN.md at workspace root
        if plan_path is None:
            workspace_root = Path(working_dir).resolve()

            # Check for legacy PLAN.md at workspace root
            legacy_plan_path = workspace_root / PLAN_FILENAME
            if legacy_plan_path.exists():
                # Use legacy location for backward compatibility
                new_recommended_path = (
                    workspace_root / DEFAULT_CONFIG_DIR / PLAN_FILENAME
                )
                logger.warning(
                    f"Found PLAN.md at legacy location {legacy_plan_path}. "
                    f"Consider moving it to {new_recommended_path} "
                    f"for consistency with OpenHands conventions."
                )
                plan_path = str(legacy_plan_path)
            else:
                # Use new default location
                plan_path = str(workspace_root / DEFAULT_CONFIG_DIR / PLAN_FILENAME)

        # Initialize PLAN.md with headers if it doesn't exist
        plan_file = Path(plan_path)
        if not plan_file.exists():
            # Import here to avoid circular imports
            from openhands.tools.preset.planning import get_plan_headers

            # Ensure parent directory exists
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(get_plan_headers())
            logger.info(f"Created new PLAN.md at {plan_path}")

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
