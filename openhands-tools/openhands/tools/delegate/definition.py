"""Delegate action and observation models for OpenHands agents."""

from typing import Literal

from pydantic import Field

from openhands.sdk.tool.tool import (
    Action,
    Observation,
)


CommandLiteral = Literal["spawn", "delegate"]


class DelegateAction(Action):
    """Schema for delegation operations."""

    command: CommandLiteral = Field(
        description="The commands to run. Allowed options are: `spawn`, `delegate`."
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


class DelegateObservation(Observation):
    """Observation from delegation operations."""

    command: CommandLiteral = Field(description="The command that was executed")
