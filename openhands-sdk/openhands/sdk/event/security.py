from typing import Any

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.security.risk import SecurityRisk


class SecurityAnalysisEvent(Event):
    """Risk levels the configured security analyzer assigned to pending actions.

    Emitted once per batch of actions the agent is about to execute, whenever a
    security analyzer is configured on the conversation, regardless of whether
    the confirmation policy then asks for confirmation. This makes the
    analyzer's verdict visible in the event log and UI even when the policy
    (for example ``NeverConfirm``) never consults it.

    This event is not shown to the LLM.
    """

    source: SourceType = "environment"

    analyzer: str = Field(
        description="Kind of the security analyzer that produced these risks."
    )
    policy: str = Field(
        description="Kind of the confirmation policy in force when the actions "
        "were analyzed."
    )
    risks: dict[EventID, SecurityRisk] = Field(
        description="Risk level per analyzed action, keyed by ActionEvent id."
    )
    details: dict[EventID, dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional analyzer-specific detail per action, keyed by "
        "ActionEvent id (for example probabilities, confidence, or a rationale).",
    )

    @property
    def visualize(self) -> Text:
        text = Text()
        text.append("Security Analysis\n", style="bold")
        text.append(f"analyzer={self.analyzer} policy={self.policy}\n")
        for action_id, risk in self.risks.items():
            text.append(f"  {action_id}: {risk.value}\n")
        return text
