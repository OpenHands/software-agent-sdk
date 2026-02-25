"""Tool result order property for API compliance monitoring."""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import LLMConvertibleEvent, ObservationBaseEvent


class ToolResultOrderProperty(APICompliancePropertyBase):
    """Detects tool results that arrive before their corresponding actions.

    Violations:
    - Tool result arrives before any action with that tool_call_id.

    Corresponds to pattern: a08 (parallel wrong order).

    Note: This is similar to UnmatchedToolResultProperty but semantically
    different - here the action may arrive later, whereas unmatched means
    the action never existed.
    """

    @property
    def name(self) -> str:
        return "tool_result_order"

    def check(
        self,
        event: LLMConvertibleEvent,
        state: ComplianceState,
    ) -> ComplianceViolation | None:
        if not isinstance(event, ObservationBaseEvent):
            return None

        # Check if we've seen an action with this tool_call_id.
        # all_tool_call_ids combines pending and completed, so a single check suffices.
        if event.tool_call_id not in state.all_tool_call_ids:
            return ComplianceViolation(
                property_name=self.name,
                event_id=event.id,
                description=(
                    f"Tool result arrived before action for tool_call_id: "
                    f"{event.tool_call_id}"
                ),
                context={"tool_call_id": event.tool_call_id},
            )

        return None
