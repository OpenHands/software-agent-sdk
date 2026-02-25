"""Duplicate tool result property for API compliance monitoring."""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import LLMConvertibleEvent, ObservationBaseEvent


class DuplicateToolResultProperty(APICompliancePropertyBase):
    """Detects duplicate tool results for the same tool_call_id.

    Violations:
    - Tool result arrives for a tool_call_id that already has a result.

    Corresponds to pattern: a05 (duplicate tool_call_id).
    """

    @property
    def name(self) -> str:
        return "duplicate_tool_result"

    def check(
        self,
        event: LLMConvertibleEvent,
        state: ComplianceState,
    ) -> ComplianceViolation | None:
        if not isinstance(event, ObservationBaseEvent):
            return None

        # Check if this tool_call_id already has a result
        if event.tool_call_id in state.completed_tool_call_ids:
            return ComplianceViolation(
                property_name=self.name,
                event_id=event.id,
                description=(
                    f"Duplicate tool result for tool_call_id: {event.tool_call_id}"
                ),
                context={"tool_call_id": event.tool_call_id},
            )

        return None
