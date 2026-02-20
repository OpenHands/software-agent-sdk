"""Event stream validation and repair.

Validates and repairs event streams to ensure they meet invariants required
for correct LLM message conversion. Repairs are deterministic and logged.

Invariants:
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


def repair_event_stream(
    events: Sequence[Event],
) -> tuple[list[Event], list[str]]:
    """Repair event stream to satisfy invariants.

    Repairs:
    1. Remove duplicate observations (keep first)
    2. Add synthetic error observations for orphan actions
    3. Remove orphan observations (no matching action)

    Args:
        events: Sequence of events to repair

    Returns:
        Tuple of (repaired_events, list of repairs made)
    """
    repairs: list[str] = []
    result: list[Event] = []

    seen_obs_tool_call_ids: set[str] = set()
    action_map: dict[
        str, tuple[int, ActionEvent]
    ] = {}  # tool_call_id -> (index, event)

    # First pass: collect actions, filter duplicate observations
    for event in events:
        if isinstance(event, ActionEvent) and event.tool_call_id:
            action_map[event.tool_call_id] = (len(result), event)
            result.append(event)

        elif isinstance(event, ObservationTypes) and event.tool_call_id:
            tc_id = event.tool_call_id

            # Skip duplicate observations
            if tc_id in seen_obs_tool_call_ids:
                repairs.append(f"Removed duplicate observation: {tc_id}")
                continue

            # Skip orphan observations (no matching action)
            if tc_id not in action_map:
                repairs.append(f"Removed orphan observation: {tc_id}")
                continue

            seen_obs_tool_call_ids.add(tc_id)
            result.append(event)

        else:
            result.append(event)

    # Second pass: add synthetic observations for orphan actions
    # Process in reverse order so insertions don't affect indices
    orphan_actions = [
        (idx, action)
        for tc_id, (idx, action) in action_map.items()
        if tc_id not in seen_obs_tool_call_ids
    ]
    orphan_actions.sort(key=lambda x: x[0], reverse=True)

    for idx, action in orphan_actions:
        synthetic = AgentErrorEvent(
            source="environment",
            tool_name=action.tool_name,
            tool_call_id=action.tool_call_id,
            error="Tool execution was interrupted. No result available.",
        )
        # Insert after the action
        result.insert(idx + 1, synthetic)
        repairs.append(
            f"Added synthetic observation for orphan action: {action.tool_call_id}"
        )

    return result, repairs


def validate_and_repair_event_stream(
    events: Sequence[Event],
) -> tuple[list[Event], list[str]]:
    """Validate event stream, repair only if needed.

    Args:
        events: Sequence of events

    Returns:
        Tuple of (events, repairs). If no repairs needed, returns list(events)
        and empty repairs list.

    Raises:
        ValueError: If repair fails to fix all issues
    """
    errors = validate_event_stream(events)
    if not errors:
        return list(events), []

    # Attempt repair
    repaired, repairs = repair_event_stream(events)

    # Validate repaired stream
    remaining_errors = validate_event_stream(repaired)
    if remaining_errors:
        raise ValueError(
            f"Event stream repair failed. "
            f"Repaired: {repairs}. Remaining errors: {remaining_errors}"
        )

    return repaired, repairs
