import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from openhands.sdk.logger import get_logger
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


logger = get_logger(__name__)
PlanStatus = Literal["pending", "in_progress", "completed"]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: str = Field(description="A concise description of the plan step.")
    status: PlanStatus = Field(description="The current status of the step.")


class UpdatePlanAction(Action):
    """Replace the current plan with a complete new snapshot."""

    explanation: str | None = Field(
        default=None, description="Optional explanation for this plan update."
    )
    plan: list[PlanStep] = Field(
        description="The complete plan snapshot. This is not an incremental patch."
    )

    @model_validator(mode="after")
    def validate_active_step(self) -> "UpdatePlanAction":
        if sum(step.status == "in_progress" for step in self.plan) > 1:
            raise ValueError("At most one plan step can be in_progress")
        return self


class UpdatePlanObservation(Observation):
    explanation: str | None = None
    plan: list[PlanStep]


class UpdatePlanExecutor(ToolExecutor[UpdatePlanAction, UpdatePlanObservation]):
    def __init__(self, save_dir: str | None = None):
        self.save_dir = Path(save_dir) if save_dir else None
        self.explanation: str | None = None
        self.plan: list[PlanStep] = []
        self._load()

    def __call__(
        self,
        action: UpdatePlanAction,
        conversation: "LocalConversation | None" = None,  # noqa: ARG002
    ) -> UpdatePlanObservation:
        self.explanation = action.explanation
        self.plan = action.plan
        self._save()
        return UpdatePlanObservation.from_text(
            "Plan updated",
            explanation=self.explanation,
            plan=self.plan,
        )

    @property
    def _plan_file(self) -> Path | None:
        return self.save_dir / "PLAN.json" if self.save_dir else None

    def _load(self) -> None:
        plan_file = self._plan_file
        if plan_file is None or not plan_file.exists():
            return
        try:
            snapshot = UpdatePlanAction.model_validate_json(
                plan_file.read_text(encoding="utf-8")
            )
            self.explanation = snapshot.explanation
            self.plan = snapshot.plan
        except (OSError, ValidationError) as e:
            logger.warning(f"Failed to load plan from {plan_file}: {e}")

    def _save(self) -> None:
        plan_file = self._plan_file
        if plan_file is None:
            return
        try:
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(
                json.dumps(
                    {
                        "explanation": self.explanation,
                        "plan": [step.model_dump() for step in self.plan],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"Failed to save plan to {plan_file}: {e}")


UPDATE_PLAN_DESCRIPTION = """Update the task plan using a complete snapshot.

Use this tool for complex, multi-step work. Provide the full plan on every call;
incremental patches are not supported. After completing a step, call this tool
again with its status set to completed and the next step set to in_progress.
Keep at most one step in_progress, and mark every step completed before finishing.
Do not use this tool for simple or single-step tasks.
"""


class UpdatePlanTool(ToolDefinition[UpdatePlanAction, UpdatePlanObservation]):
    @classmethod
    def create(cls, conv_state: "ConversationState") -> Sequence["UpdatePlanTool"]:
        return [
            cls(
                description=UPDATE_PLAN_DESCRIPTION,
                action_type=UpdatePlanAction,
                observation_type=UpdatePlanObservation,
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=UpdatePlanExecutor(save_dir=conv_state.persistence_dir),
            )
        ]


register_tool(UpdatePlanTool.name, UpdatePlanTool)
