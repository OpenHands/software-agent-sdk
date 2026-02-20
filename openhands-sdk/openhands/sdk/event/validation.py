"""Event stream validation and repair.

Validates event streams for LLM API compatibility. Provides clear errors
when issues are detected, rather than silently fixing them.

Strategy:
1. On conversation resume: Call get_repair_events() to add synthetic
   observations for orphan actions (safe - only adds events, persisted)
2. Before LLM call: Call validate_for_llm() which raises clear errors
   if issues remain (makes debugging easier, surfaces bugs)

Invariants checked:
1. Each ActionEvent has exactly one matching observation (by tool_call_id)
2. No duplicate tool_call_ids in observations
3. No orphan observations (observation without matching action)
"""

import logging
from collections.abc import Sequence

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    AgentErrorEvent,
    ObservationEvent,
    UserRejectObservation,
)


logger = logging.getLogger(__name__)

# All observation types that satisfy an action
ObservationTypes = ObservationEvent | UserRejectObservation | AgentErrorEvent


class EventStreamValidationError(Exception):
    """Raised when event stream has issues that would cause LLM API errors.

    This error indicates a bug in the event stream that needs investigation.
    The error message includes details to help debug the issue.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        message = (
            "Event stream validation failed. This would cause LLM API errors.\n"
            "Issues found:\n" + "\n".join(f"  - {e}" for e in errors) + "\n"
            "This may indicate a bug in event handling. "
            "Please report this issue with the conversation ID."
        )
        super().__init__(message)


def validate_event_stream(events: Sequence[Event]) -> list[str]:
    """Validate event stream invariants.

    Args:
        events: Sequence of events to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []

    action_tool_call_ids: dict[str, ActionEvent] = {}
    observation_tool_call_ids: set[str] = set()

    for event in events:
        if isinstance(event, ActionEvent) and event.tool_call_id:
            if event.tool_call_id in action_tool_call_ids:
                errors.append(f"Duplicate action tool_call_id: {event.tool_call_id}")
            action_tool_call_ids[event.tool_call_id] = event

        elif isinstance(event, ObservationTypes) and event.tool_call_id:
            if event.tool_call_id in observation_tool_call_ids:
                errors.append(
                    f"Duplicate observation tool_call_id: {event.tool_call_id}"
                )
            observation_tool_call_ids.add(event.tool_call_id)

    # Check for orphan actions (no observation)
    orphan_actions = set(action_tool_call_ids.keys()) - observation_tool_call_ids
    for tc_id in orphan_actions:
        errors.append(f"Orphan action (no observation): {tc_id}")

    # Check for orphan observations (no action)
    orphan_observations = observation_tool_call_ids - set(action_tool_call_ids.keys())
    for tc_id in orphan_observations:
        errors.append(f"Orphan observation (no action): {tc_id}")

    return errors


def validate_for_llm(events: Sequence[Event]) -> None:
    """Validate event stream before sending to LLM.

    Call this before events_to_messages() to catch issues early with
    clear error messages. This helps surface bugs rather than hiding them.

    Args:
        events: Sequence of events to validate

    Raises:
        EventStreamValidationError: If validation fails with details about issues
    """
    errors = validate_event_stream(events)
    if errors:
        raise EventStreamValidationError(errors)


def get_repair_events(events: Sequence[Event]) -> list[AgentErrorEvent]:
    """Get synthetic events to persist for orphan actions.

    For orphan actions (tool calls without observations), creates synthetic
    AgentErrorEvent to inform the LLM the tool execution was interrupted.

    This is the ONLY safe repair: adding events. It should be called on
    conversation resume and the events should be persisted to the event store.

    Other issues (duplicate observations, orphan observations) are NOT
    repaired here because:
    - Removing events could hide bugs
    - These issues indicate problems that should be investigated

    Args:
        events: Sequence of events to analyze

    Returns:
        List of AgentErrorEvent to append to the event store.
        Empty list if no orphan actions found.
    """
    action_tool_call_ids: dict[str, ActionEvent] = {}
    observation_tool_call_ids: set[str] = set()

    for event in events:
        if isinstance(event, ActionEvent) and event.tool_call_id:
            action_tool_call_ids[event.tool_call_id] = event
        elif isinstance(event, ObservationTypes) and event.tool_call_id:
            observation_tool_call_ids.add(event.tool_call_id)

    # Find orphan actions
    orphan_tool_call_ids = set(action_tool_call_ids.keys()) - observation_tool_call_ids

    # Create synthetic error events for each orphan
    synthetic_events: list[AgentErrorEvent] = []
    for tc_id in orphan_tool_call_ids:
        action = action_tool_call_ids[tc_id]
        synthetic = AgentErrorEvent(
            source="environment",
            tool_name=action.tool_name,
            tool_call_id=action.tool_call_id,
            error=(
                "Tool execution was interrupted due to a system restart. "
                "The tool did not complete and no result is available."
            ),
        )
        synthetic_events.append(synthetic)
        logger.warning(
            f"Created synthetic observation for orphan action: "
            f"tool={action.tool_name}, tool_call_id={tc_id}"
        )

    return synthetic_events
