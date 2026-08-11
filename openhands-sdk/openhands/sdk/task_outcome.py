"""Structured task outcome models for conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


TaskOutcomeStatus = Literal[
    "success",
    "partial_success",
    "blocked",
    "failed",
    "unknown",
]
TaskOutcomeSource = Literal[
    "agent_report",
    "system",
    "critic",
    "judge",
    "manual",
]


class TaskOutcomeBlocker(BaseModel):
    """A blocker that prevented or limited task completion."""

    type: str = Field(
        description=(
            "Short machine-readable blocker category, e.g. missing_secret, "
            "permission_denied, external_service, timeout, or unclear_requirements."
        )
    )
    message: str = Field(description="Human-readable blocker description.")
    recoverable: bool | None = Field(
        default=None,
        description="Whether user or system action can reasonably unblock the task.",
    )


class TaskOutcome(BaseModel):
    """Latest semantic outcome reported for a conversation task."""

    status: TaskOutcomeStatus = Field(
        description="Agent's semantic assessment of task completion."
    )
    summary: str = Field(description="Concise summary of the outcome.")
    blockers: list[TaskOutcomeBlocker] = Field(
        default_factory=list,
        description="Blockers encountered while trying to complete the task.",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence in the semantic outcome, from 0 to 1.",
    )
    needs_user_action: bool = Field(
        default=False,
        description="Whether the user needs to act before the task can proceed.",
    )
    source: TaskOutcomeSource = Field(
        default="agent_report",
        description="Where this outcome came from.",
    )
    reported_at: datetime | None = Field(
        default=None,
        description="When this outcome was recorded by the runtime.",
    )
    terminal_reason: str | None = Field(
        default=None,
        description=(
            "Optional terminal condition that produced this outcome, e.g. "
            "finish_action, exception, timeout, cancelled, max_iterations, or stuck."
        ),
    )
