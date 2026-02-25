"""Interleaved message property for API compliance monitoring."""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import (
    ActionEvent,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationBaseEvent,
)


class InterleavedMessageProperty(APICompliancePropertyBase):
    """Detects messages interleaved between tool_use and tool_result.

    Violations:
    - User or assistant message arrives while tool calls are pending.

    Corresponds to patterns: a01 (unmatched tool_use), a03 (interleaved user),
    a04 (interleaved assistant), a07 (parallel missing result).
    """

    @property
    def name(self) -> str:
        return "interleaved_message"

    def check(
        self,
        event: LLMConvertibleEvent,
        state: ComplianceState,
    ) -> ComplianceViolation | None:
        # Only check MessageEvent
        if not isinstance(event, MessageEvent):
            return None

        # Violation if there are pending tool calls
        if state.pending_tool_call_ids:
            pending_ids = list(state.pending_tool_call_ids.keys())
            return ComplianceViolation(
                property_name=self.name,
                event_id=event.id,
                description=(
                    f"Message event interleaved with {len(pending_ids)} pending "
                    f"tool call(s)"
                ),
                context={"pending_tool_call_ids": pending_ids},
            )

        return None

    def update_state(self, event: LLMConvertibleEvent, state: ComplianceState) -> None:
        """Track pending and completed tool calls."""
        if isinstance(event, ActionEvent):
            state.pending_tool_call_ids[event.tool_call_id] = event.id
        elif isinstance(event, ObservationBaseEvent):
            # Move from pending to completed
            state.pending_tool_call_ids.pop(event.tool_call_id, None)
            state.completed_tool_call_ids.add(event.tool_call_id)
