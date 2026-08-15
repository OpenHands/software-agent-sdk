"""Delegate action and observation models for OpenHands agents."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from openhands.sdk.tool.tool import (
    Action,
    Observation,
)


CommandLiteral = Literal["spawn", "delegate", "status", "output", "stop"]


class DelegateTaskStatus(StrEnum):
    """Lifecycle states for an in-process background delegation task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DelegateAction(Action):
    """Schema for delegation operations."""

    command: CommandLiteral = Field(
        description=(
            "The command to run. Allowed options are: `spawn`, `delegate`, "
            "`status`, `output`, and `stop`."
        )
    )
    ids: list[str] | None = Field(
        default=None,
        description="Required parameter of `spawn` command. "
        "List of identifiers to initialize sub-agents with.",
    )
    agent_types: list[str] | None = Field(
        default=None,
        description=(
            "Optional parameter of `spawn` command. "
            "List of agent types for each ID (e.g., ['researcher', 'programmer']). "
            "If omitted or blank for an ID, the default general-purpose agent is used."
        ),
    )
    tasks: dict[str, str] | None = Field(
        default=None,
        description=(
            "Required parameter of `delegate` command. "
            "Dictionary mapping sub-agent identifiers to task descriptions."
        ),
    )
    background: bool = Field(
        default=False,
        description=(
            "Optional parameter of `delegate`. When true, start each task in the "
            "background and return task IDs immediately."
        ),
    )
    task_id: str | None = Field(
        default=None,
        description="Required parameter of `status`, `output`, and `stop`.",
    )


class DelegateObservation(Observation):
    """Observation from delegation operations."""

    command: CommandLiteral = Field(description="The command that was executed")
    task_ids: dict[str, str] | None = Field(
        default=None,
        description=(
            "Background task IDs keyed by sub-agent identifier for a `delegate` "
            "command."
        ),
    )
    task_id: str | None = Field(
        default=None,
        description="Background task ID for a lifecycle command.",
    )
    agent_id: str | None = Field(
        default=None,
        description="Sub-agent identifier associated with a background task.",
    )
    status: DelegateTaskStatus | None = Field(
        default=None,
        description="Current background task lifecycle status.",
    )
