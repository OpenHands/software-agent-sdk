from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.task_outcome import (
    TaskOutcome,
    TaskOutcomeBlocker,
    TaskOutcomeStatus,
)
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from openhands.sdk.utils import utc_now


if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState


class ReportTaskOutcomeAction(Action):
    """Action for recording semantic task outcome metadata."""

    status: TaskOutcomeStatus = Field(
        description=(
            "Semantic task outcome: success, partial_success, blocked, failed, "
            "or unknown. This is separate from process/runtime status."
        )
    )
    summary: str = Field(description="Concise summary of what happened.")
    blockers: list[TaskOutcomeBlocker] = Field(
        default_factory=list,
        description="Any blockers that prevented or limited task completion.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence in this assessment, from 0 to 1.",
    )
    needs_user_action: bool = Field(
        default=False,
        description="Whether the user needs to act before the task can proceed.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Task outcome: ", style="bold blue")
        content.append(self.status)
        content.append("\n")
        content.append(self.summary)
        return content


class ReportTaskOutcomeObservation(Observation):
    """Observation returned after recording task outcome metadata."""

    outcome: TaskOutcome = Field(description="The recorded task outcome.")

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Recorded task outcome: ", style="bold green")
        content.append(self.outcome.status)
        content.append("\n")
        content.append(self.outcome.summary)
        return content


TOOL_DESCRIPTION = """Report structured task outcome metadata for the current
conversation.

Use this tool to keep the platform updated on whether the task is succeeding,
blocked, failed, or only partially complete. This does not end the conversation;
it only records metadata. You may call it multiple times as the task progresses.
The latest report is stored on conversation metadata, while every call remains in
the conversation event history as a tool call.

Before you finish the task, call this with an accurate status:
- success: the requested task was completed
- partial_success: useful progress was made, but some work remains
- blocked: external input, credentials, permissions, or unclear requirements
  prevent completion
- failed: the task could not be completed due to an unrecoverable error
- unknown: you cannot confidently determine the task outcome

Include blockers when status is blocked, failed, or partial_success.
"""


class ReportTaskOutcomeExecutor(ToolExecutor):
    def __call__(
        self,
        action: ReportTaskOutcomeAction,
        conversation: BaseConversation | None = None,
    ) -> ReportTaskOutcomeObservation:
        if conversation is None:
            outcome = TaskOutcome(
                status="failed",
                summary="Could not record task outcome without conversation state.",
                blockers=[
                    TaskOutcomeBlocker(
                        type="runtime_error",
                        message="The report_task_outcome tool had no conversation.",
                    )
                ],
                reported_at=utc_now(),
            )
            return ReportTaskOutcomeObservation.from_text(
                text=outcome.summary,
                is_error=True,
                outcome=outcome,
            )

        outcome = TaskOutcome(
            status=action.status,
            summary=action.summary,
            blockers=action.blockers,
            confidence=action.confidence,
            needs_user_action=action.needs_user_action,
            reported_at=utc_now(),
        )
        with conversation.state:
            conversation.state.task_outcome = outcome

        return ReportTaskOutcomeObservation.from_text(
            text=f"Task outcome recorded: {outcome.status}",
            outcome=outcome,
        )


class ReportTaskOutcomeTool(
    ToolDefinition[ReportTaskOutcomeAction, ReportTaskOutcomeObservation]
):
    """Built-in tool for recording task outcome metadata."""

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("ReportTaskOutcomeTool doesn't accept params")
        return [
            cls(
                action_type=ReportTaskOutcomeAction,
                observation_type=ReportTaskOutcomeObservation,
                description=TOOL_DESCRIPTION,
                executor=ReportTaskOutcomeExecutor(),
                annotations=ToolAnnotations(
                    title="report_task_outcome",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]
