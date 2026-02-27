"""API Compliance Monitor that checks events before adding to conversation."""

from openhands.sdk.conversation.compliance.base import (
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.event import (
    ActionEvent,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationBaseEvent,
)
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class APIComplianceMonitor:
    """Monitors events for API compliance violations.

    Enforces valid tool-call sequences by checking what events are allowed
    given current state. The key invariant: when tool calls are pending,
    only matching observations are allowed.

    State machine:
    - IDLE (no pending calls): Messages and new actions allowed
    - TOOL_CALLING (pending calls): Only matching observations allowed

    Currently operates in observation mode (violations are logged but events
    are still processed).

    Attributes:
        state: Compliance state tracking pending/completed tool calls.
    """

    def __init__(self) -> None:
        """Initialize the compliance monitor."""
        self.state = ComplianceState()

    def _check_tool_call_sequence(
        self, event: LLMConvertibleEvent
    ) -> ComplianceViolation | None:
        """Check if an event violates the tool-call sequence property.

        The rule is simple: if we have pending tool calls, only matching
        observations are allowed. This covers all 8 API compliance patterns:

        - a01 (unmatched_tool_use): Message while calls pending
        - a02 (unmatched_tool_result): Result with unknown ID
        - a03 (interleaved_user_msg): User message while calls pending
        - a04 (interleaved_asst_msg): Assistant message while calls pending
        - a05 (duplicate_tool_call_id): Result for already-completed ID
        - a06 (wrong_tool_call_id): Result with wrong/unknown ID
        - a07 (parallel_missing_result): Message before all parallel results
        - a08 (parallel_wrong_order): Result before action (unknown ID)

        Args:
            event: The event to check.

        Returns:
            A ComplianceViolation if the event violates the property, None otherwise.
        """
        # Actions are always allowed - they start or continue a tool-call batch
        if isinstance(event, ActionEvent):
            return None

        # Messages require no pending tool calls
        if isinstance(event, MessageEvent):
            if self.state.pending_tool_call_ids:
                pending_ids = list(self.state.pending_tool_call_ids.keys())
                return ComplianceViolation(
                    property_name="interleaved_message",
                    event_id=event.id,
                    description=(
                        f"Message interleaved with {len(pending_ids)} pending "
                        f"tool call(s)"
                    ),
                    context={"pending_tool_call_ids": pending_ids},
                )
            return None

        # Observations must match a known tool_call_id
        if isinstance(event, ObservationBaseEvent):
            tool_call_id = event.tool_call_id

            # Check for valid match (pending)
            if tool_call_id in self.state.pending_tool_call_ids:
                return None  # Valid - completes a pending call

            # Check for duplicate (already completed)
            if tool_call_id in self.state.completed_tool_call_ids:
                return ComplianceViolation(
                    property_name="duplicate_tool_result",
                    event_id=event.id,
                    description=(
                        f"Duplicate tool result for tool_call_id: {tool_call_id}"
                    ),
                    context={"tool_call_id": tool_call_id},
                )

            # Unknown ID - orphan result (covers a02, a06, a08)
            return ComplianceViolation(
                property_name="unmatched_tool_result",
                event_id=event.id,
                description=(
                    f"Tool result references unknown tool_call_id: {tool_call_id}"
                ),
                context={"tool_call_id": tool_call_id},
            )

        return None

    def _update_state(self, event: LLMConvertibleEvent) -> None:
        """Update compliance state after processing an event.

        Tracks the tool-call lifecycle:
        - ActionEvent: Add to pending
        - ObservationBaseEvent: Move from pending to completed
        """
        if isinstance(event, ActionEvent):
            self.state.pending_tool_call_ids[event.tool_call_id] = event.id
        elif isinstance(event, ObservationBaseEvent):
            # Move from pending to completed (if it was pending)
            self.state.pending_tool_call_ids.pop(event.tool_call_id, None)
            self.state.completed_tool_call_ids.add(event.tool_call_id)

    def process_event(self, event: LLMConvertibleEvent) -> list[ComplianceViolation]:
        """Check an event for violations and update state.

        Args:
            event: The event to process.

        Returns:
            List of violations detected (empty if compliant).
        """
        violations: list[ComplianceViolation] = []

        try:
            violation = self._check_tool_call_sequence(event)
            if violation is not None:
                violations.append(violation)
                logger.warning(
                    "API compliance violation detected: %s - %s (event_id=%s)",
                    violation.property_name,
                    violation.description,
                    violation.event_id,
                )
        except Exception as e:
            logger.exception(
                "Error checking compliance for event %s: %s",
                event.id,
                e,
            )

        try:
            self._update_state(event)
        except Exception as e:
            logger.exception(
                "Error updating compliance state for event %s: %s",
                event.id,
                e,
            )

        return violations
