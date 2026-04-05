from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from rich.text import Text

from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)
from openhands.tools.task_tracker.definition import (
    TaskItem,
    TaskTrackerAction,
    TaskTrackerExecutor,
    TaskTrackerStatusType,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


UpdatePlanStatusType = Literal["pending", "in_progress", "completed"]

_STATUS_MAP: dict[UpdatePlanStatusType, TaskTrackerStatusType] = {
    "pending": "todo",
    "in_progress": "in_progress",
    "completed": "done",
}


class PlanItem(BaseModel):
    step: str = Field(..., description="A short plan step.")
    status: UpdatePlanStatusType = Field(
        ..., description="One of: pending, in_progress, completed."
    )


class UpdatePlanAction(Action):
    explanation: str | None = Field(
        default=None,
        description="Optional explanation for why the plan changed.",
    )
    plan: list[PlanItem] = Field(
        default_factory=list,
        description="The updated task plan.",
    )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append("📋 ", style="green")
        text.append("Update Plan", style="green")
        if self.plan:
            text.append(f" ({len(self.plan)} steps)")
        if self.explanation:
            text.append(f"\n{self.explanation}", style="italic")
        return text


class UpdatePlanObservation(Observation):
    explanation: str | None = Field(
        default=None,
        description="Explanation supplied with the plan update.",
    )
    plan: list[PlanItem] = Field(default_factory=list, description="The current plan.")

    @property
    def visualize(self) -> Text:
        text = Text()
        if self.is_error:
            text.append("❌ ", style="red bold")
            text.append(self.ERROR_MESSAGE_HEADER, style="bold red")

        if self.plan:
            text.append("📋 Plan updated", style="green")
            if self.explanation:
                text.append(f": {self.explanation}", style="italic")
            text.append("\n\n")
            for index, item in enumerate(self.plan, start=1):
                icon = {
                    "pending": "⏳",
                    "in_progress": "🔄",
                    "completed": "✅",
                }[item.status]
                text.append(f"{icon} {index}. {item.step}")
                if index < len(self.plan):
                    text.append("\n")
        else:
            text.append("📝 Plan is empty")
        return text


class UpdatePlanExecutor(ToolExecutor[UpdatePlanAction, UpdatePlanObservation]):
    def __init__(self, save_dir: str | None = None):
        self._task_tracker = TaskTrackerExecutor(save_dir=save_dir)

    def __call__(
        self,
        action: UpdatePlanAction,
        conversation: "LocalConversation | None" = None,
    ) -> UpdatePlanObservation:
        tracker_observation = self._task_tracker(
            TaskTrackerAction(
                command="plan",
                task_list=[self._to_task_item(item) for item in action.plan],
            ),
            conversation,
        )
        text = (
            tracker_observation.text or f"Plan updated with {len(action.plan)} step(s)."
        )
        return UpdatePlanObservation.from_text(
            text=text,
            is_error=tracker_observation.is_error,
            explanation=action.explanation,
            plan=action.plan,
        )

    @staticmethod
    def _to_task_item(item: PlanItem) -> TaskItem:
        return TaskItem(
            title=item.step,
            notes="",
            status=_STATUS_MAP[item.status],
        )


_UPDATE_PLAN_DESCRIPTION = (
    "Updates the task plan. Provide an optional explanation and a list of plan "
    "items, each with a step and status. At most one step can be in_progress "
    "at a time."
)


class UpdatePlanTool(ToolDefinition[UpdatePlanAction, UpdatePlanObservation]):
    @classmethod
    def create(cls, conv_state: "ConversationState") -> Sequence["UpdatePlanTool"]:
        executor = UpdatePlanExecutor(save_dir=conv_state.persistence_dir)
        return [
            cls(
                description=_UPDATE_PLAN_DESCRIPTION,
                action_type=UpdatePlanAction,
                observation_type=UpdatePlanObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


register_tool(UpdatePlanTool.name, UpdatePlanTool)
