"""API Compliance Monitor that checks events before adding to conversation."""

from openhands.sdk.conversation.compliance.base import (
    APICompliancePropertyBase,
    ComplianceState,
    ComplianceViolation,
)
from openhands.sdk.conversation.compliance.properties import ALL_COMPLIANCE_PROPERTIES
from openhands.sdk.event import LLMConvertibleEvent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


class APIComplianceMonitor:
    """Monitors events for API compliance violations.

    This monitor checks incoming events against a set of compliance properties
    and logs any violations. Currently operates in observation mode (violations
    are logged but events are still processed). Future versions may support
    per-property reconciliation strategies.

    Attributes:
        state: Shared compliance state tracking tool calls.
        properties: List of compliance properties to check.
    """

    def __init__(
        self,
        properties: list[APICompliancePropertyBase] | None = None,
    ) -> None:
        """Initialize the compliance monitor.

        Args:
            properties: Compliance properties to check. If None, uses all
                default properties.
        """
        self.state = ComplianceState()
        self.properties = (
            properties if properties is not None else list(ALL_COMPLIANCE_PROPERTIES)
        )

    def check_event(self, event: LLMConvertibleEvent) -> list[ComplianceViolation]:
        """Check an event for compliance violations.

        This method checks the event against all properties and returns any
        violations found. It does NOT update state - call process_event()
        after adding the event to update tracking state.

        Args:
            event: The event to check.

        Returns:
            List of violations detected (empty if compliant).
        """
        violations: list[ComplianceViolation] = []

        for prop in self.properties:
            try:
                violation = prop.check(event, self.state)
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
                    "Error checking compliance property %s for event %s: %s",
                    prop.name,
                    event.id,
                    e,
                )

        return violations

    def update_state(self, event: LLMConvertibleEvent) -> None:
        """Update compliance state after an event is processed.

        Call this after the event has been added to the event log.

        Args:
            event: The event that was just processed.
        """
        for prop in self.properties:
            try:
                prop.update_state(event, self.state)
            except Exception as e:
                logger.exception(
                    "Error updating state for property %s on event %s: %s",
                    prop.name,
                    event.id,
                    e,
                )

    def process_event(self, event: LLMConvertibleEvent) -> list[ComplianceViolation]:
        """Check an event and update state.

        This is a convenience method that combines check_event() and
        update_state(). Use this when you want to check and track in
        one call.

        Args:
            event: The event to process.

        Returns:
            List of violations detected (empty if compliant).
        """
        violations = self.check_event(event)
        self.update_state(event)
        return violations
