"""Unmatched tool result property for API compliance monitoring."""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import LLMConvertibleEvent, ObservationBaseEvent


class UnmatchedToolResultProperty(APICompliancePropertyBase):
    """Detects tool results that reference unknown tool_call_ids.

    Violations:
    - Tool result references a tool_call_id that was never seen.

    Corresponds to patterns: a02 (unmatched tool_result), a06 (wrong tool_call_id).
    """

    @property
    def name(self) -> str:
        return "unmatched_tool_result"

    def check(
        self,
        event: LLMConvertibleEvent,
        state: ComplianceState,
    ) -> ComplianceViolation | None:
        if not isinstance(event, ObservationBaseEvent):
            return None

        # Check if tool_call_id was ever seen
        if event.tool_call_id not in state.all_tool_call_ids:
            return ComplianceViolation(
                property_name=self.name,
                event_id=event.id,
                description=(
                    f"Tool result references unknown tool_call_id: {event.tool_call_id}"
                ),
                context={"tool_call_id": event.tool_call_id},
            )

        return None
