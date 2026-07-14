from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator
from rich.text import Text

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


class FinishAction(Action):
    message: str = Field(description="Final message to send to the user.")
    outcome: Literal["success", "infeasible"] = Field(
        default="success",
        description=(
            "Outcome of the task. Use 'success' when the task was completed, "
            "or 'infeasible' when the task cannot be completed as requested."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Reason the task is infeasible. Required (non-empty) when outcome "
            "is 'infeasible'."
        ),
    )

    @model_validator(mode="after")
    def _validate_reason(self) -> Self:
        if self.reason is not None and not self.reason.strip():
            raise ValueError("reason must be a non-empty string when provided")
        if self.outcome == "infeasible" and self.reason is None:
            raise ValueError("reason is required when outcome is 'infeasible'")
        return self

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation of this action."""
        content = Text()
        if self.outcome == "infeasible":
            content.append("Finish (infeasible) with message:\n", style="bold red")
            content.append(self.message)
            if self.reason:
                content.append("\nReason: ", style="bold red")
                content.append(self.reason)
        else:
            content.append("Finish with message:\n", style="bold blue")
            content.append(self.message)
        return content


class FinishObservation(Observation):
    """
    Observation returned after finishing a task.
    The FinishAction itself contains the message sent to the user so no
    extra fields are needed here.
    """

    @property
    def visualize(self) -> Text:
        """Return an empty Text representation since the message is in the action."""
        return Text()


TOOL_DESCRIPTION = """Signals the completion of the current task or conversation.

Use this tool when:
- You have successfully completed the user's requested task
- You cannot proceed further due to technical limitations or missing information

Set `outcome` to declare the result explicitly:
- "success" (default): the task was completed as requested
- "infeasible": the task cannot be completed as requested; in this case
  `reason` must be provided with a non-empty explanation of why

The message should include:
- A clear summary of actions taken and their results
- Any next steps for the user
- Explanation if you're unable to complete the task
- Any follow-up questions if more information is needed
"""


class FinishExecutor(ToolExecutor):
    def __call__(
        self,
        action: FinishAction,
        conversation: "BaseConversation | None" = None,  # noqa: ARG002
    ) -> FinishObservation:
        return FinishObservation.from_text(text=action.message)


class FinishTool(ToolDefinition[FinishAction, FinishObservation]):
    """Tool for signaling the completion of a task or conversation."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        """Create FinishTool instance.

        Args:
            conv_state: Optional conversation state (not used by FinishTool).
            **params: Additional parameters (none supported).

        Returns:
            A sequence containing a single FinishTool instance.

        Raises:
            ValueError: If any parameters are provided.
        """
        if params:
            raise ValueError("FinishTool doesn't accept parameters")
        return [
            cls(
                action_type=FinishAction,
                observation_type=FinishObservation,
                description=TOOL_DESCRIPTION,
                executor=FinishExecutor(),
                annotations=ToolAnnotations(
                    title="finish",
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
            )
        ]
