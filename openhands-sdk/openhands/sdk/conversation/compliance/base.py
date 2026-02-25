"""Base classes for API compliance monitoring."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from openhands.sdk.event import Event
from openhands.sdk.event.types import EventID, ToolCallID


@dataclass
class ComplianceViolation:
    """Represents an API compliance violation.

    Attributes:
        property_name: Name of the property that was violated.
        event_id: ID of the event that caused the violation.
        description: Human-readable description of the violation.
        context: Optional additional context (e.g., related tool_call_ids).
    """

    property_name: str
    event_id: EventID
    description: str
    context: dict[str, object] | None = None


@dataclass
class ComplianceState:
    """Shared state for tracking API compliance across properties.

    This state is updated as events are processed and provides the context
    needed by individual properties to detect violations.

    Attributes:
        pending_tool_call_ids: Tool calls that have been made but not yet
            received results. Maps tool_call_id to the ActionEvent id.
        completed_tool_call_ids: Tool calls that have received results.
            Used to detect duplicate results.
    """

    pending_tool_call_ids: dict[ToolCallID, EventID] = field(default_factory=dict)
    completed_tool_call_ids: set[ToolCallID] = field(default_factory=set)

    @property
    def all_tool_call_ids(self) -> set[ToolCallID]:
        """All tool_call_ids seen so far (pending or completed).

        This is derived from pending_tool_call_ids (dict keys) and
        completed_tool_call_ids to avoid maintaining redundant state.
        """
        return set(self.pending_tool_call_ids) | self.completed_tool_call_ids


class APICompliancePropertyBase(ABC):
    """Base class for API compliance properties.

    Each property represents a single compliance rule that LLM APIs expect.
    Properties check incoming events for violations and update shared state.

    The design allows for future per-property reconciliation strategies
    while currently defaulting to logging violations.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this property.

        Used in violation reports and logging.
        """

    @abstractmethod
    def check(
        self,
        event: Event,
        state: ComplianceState,
    ) -> ComplianceViolation | None:
        """Check if an event violates this property.

        Args:
            event: The event about to be added to the conversation.
            state: Current compliance state for context.

        Returns:
            A ComplianceViolation if the event violates this property,
            None otherwise.
        """

    def update_state(self, event: Event, state: ComplianceState) -> None:
        """Update compliance state after an event is processed.

        Override this method if the property needs to track state.
        Default implementation does nothing.

        Args:
            event: The event that was just processed.
            state: The compliance state to update.
        """
