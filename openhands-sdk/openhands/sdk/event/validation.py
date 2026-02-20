"""Event stream validation and repair.

Validates and repairs event streams to ensure they meet invariants required
for correct LLM message conversion. Repairs are deterministic and logged.

Invariants:
1. Each ActionEvent has exactly one matching observation (by tool_call_id)
2. No duplicate tool_call_ids in observations
3. No orphan observations (observation without matching action)

Primary API:
- prepare_events_for_llm(): Single function that fixes ALL issues before LLM call
- get_repair_events(): Returns synthetic events to persist on conversation resume
- validate_event_stream(): Detect issues without fixing
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


def prepare_events_for_llm(events: Sequence[Event]) -> tuple[list[Event], list[str]]:
    """Prepare events for LLM by fixing ALL validation issues.

    Handles all cases that would cause LLM API errors:
    1. Orphan actions (no observation) → Adds synthetic AgentErrorEvent
    2. Duplicate observations → Keeps first, removes subsequent
    3. Orphan observations (no action) → Removes

    This is the primary function to call before events_to_messages().

    Args:
        events: Sequence of events to prepare

    Returns:
        Tuple of (prepared_events, list of modifications made)

    Example:
        prepared, mods = prepare_events_for_llm(state.events)
        messages = LLMConvertibleEvent.events_to_messages(prepared)
    """
    modifications: list[str] = []
    result: list[Event] = []

    seen_obs_tool_call_ids: set[str] = set()
    action_map: dict[
        str, tuple[int, ActionEvent]
    ] = {}  # tool_call_id -> (index, event)

    # Single pass: collect actions, filter observations
    for event in events:
        if isinstance(event, ActionEvent) and event.tool_call_id:
            action_map[event.tool_call_id] = (len(result), event)
            result.append(event)

        elif isinstance(event, ObservationTypes) and event.tool_call_id:
            tc_id = event.tool_call_id

            # Skip duplicate observations (keep first)
            if tc_id in seen_obs_tool_call_ids:
                modifications.append(f"Removed duplicate observation: {tc_id}")
                continue

            # Skip orphan observations (no matching action)
            if tc_id not in action_map:
                modifications.append(f"Removed orphan observation: {tc_id}")
                continue

            seen_obs_tool_call_ids.add(tc_id)
            result.append(event)

        else:
            result.append(event)

    # Add synthetic observations for orphan actions
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
            error=(
                "Tool execution was interrupted. "
                "The tool did not complete and no result is available."
            ),
        )
        # Insert after the action
        result.insert(idx + 1, synthetic)
        modifications.append(
            f"Added synthetic observation for orphan action: {action.tool_call_id}"
        )

    if modifications:
        logger.warning(f"Prepared events for LLM: {modifications}")

    return result, modifications


def get_repair_events(events: Sequence[Event]) -> list[AgentErrorEvent]:
    """Get synthetic events to persist for orphan actions.

    For orphan actions (tool calls without observations), creates synthetic
    AgentErrorEvent to inform the LLM the tool execution was interrupted.

    Use on conversation resume to persist these repairs to the event store.
    For one-time LLM preparation, use prepare_events_for_llm() instead.

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
