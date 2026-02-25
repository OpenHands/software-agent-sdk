"""Concrete implementations of API compliance properties.

Each property corresponds to one or more API compliance patterns:
- InterleavedMessageProperty: a01, a03, a04, a07
- UnmatchedToolResultProperty: a02, a06
- DuplicateToolResultProperty: a05
- ToolResultOrderProperty: a08
"""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import ActionEvent, Event, MessageEvent, ObservationBaseEvent


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
        event: Event,
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

    def update_state(self, event: Event, state: ComplianceState) -> None:
        """Track pending and completed tool calls."""
        if isinstance(event, ActionEvent):
            state.pending_tool_call_ids[event.tool_call_id] = event.id
        elif isinstance(event, ObservationBaseEvent):
            # Move from pending to completed
            state.pending_tool_call_ids.pop(event.tool_call_id, None)
            state.completed_tool_call_ids.add(event.tool_call_id)


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
        event: Event,
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
        event: Event,
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
        event: Event,
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


# All properties in recommended check order
ALL_COMPLIANCE_PROPERTIES: list[APICompliancePropertyBase] = [
    ToolResultOrderProperty(),
    UnmatchedToolResultProperty(),
    DuplicateToolResultProperty(),
    InterleavedMessageProperty(),
]
