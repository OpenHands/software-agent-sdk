"""Experimental structured task outcome models for conversation finish actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TaskOutcomeStatus = Literal[
    "success",
    "partial_success",
    "blocked",
    "failed",
    "unknown",
]


class TaskOutcome(BaseModel):
    """Latest semantic outcome reported for a conversation task.

    Experimental: this structured response model may change as task outcome
    reporting is refined.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: TaskOutcomeStatus = Field(
        description="Agent's semantic assessment of task completion."
    )
    summary: str = Field(
        alias="outcome_summary",
        description=(
            "Concise outcome summary. Include what was completed, blockers, "
            "required user action, and relevant next steps."
        ),
    )
