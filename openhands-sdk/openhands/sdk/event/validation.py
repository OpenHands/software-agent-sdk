"""Event stream validation and repair.

Validates and repairs event streams to ensure they meet invariants required
for correct LLM message conversion. Repairs are deterministic and logged.

Invariants:
1. Each ActionEvent has exactly one matching observation (by tool_call_id)
2. No duplicate tool_call_ids in observations
3. No orphan observations (observation without matching action)

Usage:
    # On conversation resume, call repair and persist synthetic events
    synthetic_events = get_repair_events(state.events)
    for event in synthetic_events:
        conversation._on_event(event)  # Persists to event store
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


def get_repair_events(events: Sequence[Event]) -> list[AgentErrorEvent]:
    """Get synthetic events needed to repair an invalid event stream.

    This function returns NEW events that should be appended to the event store
    to fix orphan actions. It does NOT modify existing events.

    For orphan actions (tool calls without observations), creates synthetic
    AgentErrorEvent to inform the LLM the tool execution was interrupted.

    Note: Duplicate observations are NOT repaired here because they require
    removing events from the store, which is more complex. The LLM can handle
    seeing duplicate tool results.

    Args:
        events: Sequence of events to analyze

    Returns:
        List of AgentErrorEvent to append to the event store.
        Empty list if no repairs needed.
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
