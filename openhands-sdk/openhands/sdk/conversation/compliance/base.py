"""Base classes for API compliance monitoring."""

from dataclasses import dataclass, field

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
    """Shared state for tracking API compliance.

    Tracks the tool call lifecycle to detect violations:
    - pending_tool_call_ids: Actions awaiting results
    - completed_tool_call_ids: Actions that have received results

    Attributes:
        pending_tool_call_ids: Tool calls that have been made but not yet
            received results. Maps tool_call_id to the ActionEvent id.
        completed_tool_call_ids: Tool calls that have received results.
            Used to detect duplicate results.
    """

    pending_tool_call_ids: dict[ToolCallID, EventID] = field(default_factory=dict)
    completed_tool_call_ids: set[ToolCallID] = field(default_factory=set)
