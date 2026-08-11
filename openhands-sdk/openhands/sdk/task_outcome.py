"""Structured task outcome models for conversations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from openhands.sdk.event.error_classification import ErrorClassification, FailureKind
from openhands.sdk.utils import utc_now


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


class FinishTaskOutcomeResponse(BaseModel):
    """Structured response fields returned alongside FinishTool arguments."""

    task_outcome: TaskOutcome = Field(
        description=(
            "Structured assessment of whether the task was completed, including "
            "any blockers or user action needed."
        )
    )


def task_outcome_from_finish(outcome: TaskOutcome) -> TaskOutcome:
    """Normalize an agent-authored outcome reported via FinishTool."""
    return outcome.model_copy(
        update={
            "source": "agent_report",
            "reported_at": utc_now(),
            "terminal_reason": "finish_action",
        }
    )


def task_outcome_from_error(
    *,
    code: str,
    detail: str,
    classification: ErrorClassification | None = None,
    terminal_reason: str | None = None,
) -> TaskOutcome:
    """Build a system-authored outcome for harness/runtime failures."""
    status: TaskOutcomeStatus = "failed"
    needs_user_action = False
    recoverable: bool | None = None
    blocker_type = code

    if classification is not None:
        blocker_type = classification.kind.value
        recoverable = classification.retryable or classification.user_action != "none"
        needs_user_action = classification.user_action == "settings"
        if classification.kind in {
            FailureKind.AUTH,
            FailureKind.QUOTA,
            FailureKind.CONFIG,
            FailureKind.RATE_LIMIT,
            FailureKind.TRANSIENT,
        }:
            status = "blocked"

    summary = detail or f"Conversation failed with {code}."
    return TaskOutcome(
        status=status,
        summary=summary,
        blockers=[
            TaskOutcomeBlocker(
                type=blocker_type,
                message=summary,
                recoverable=recoverable,
            )
        ],
        needs_user_action=needs_user_action,
        source="system",
        reported_at=utc_now(),
        terminal_reason=terminal_reason or code,
    )
