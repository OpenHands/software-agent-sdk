"""Event stream validation and repair.

Validates event streams for LLM API compatibility. Provides clear errors
when issues are detected, rather than silently fixing them.

Integration points:
1. On conversation resume: ConversationState.create_or_restore() calls
   get_repair_events() and persists synthetic events for orphan actions
2. Before LLM call: prepare_llm_messages() calls validate_for_llm() which
   raises EventStreamValidationError with clear message for frontend

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


class EventStreamValidationError(Exception):
    """Raised when event stream has issues that would cause LLM API errors.

    This error indicates a bug in the event stream that needs investigation.
    The error message includes details to help debug the issue.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        issues = "\n".join(f"  - {e}" for e in errors)
        self.message = (
            f"Event stream validation failed. This would cause LLM API errors.\n"
            f"Issues found:\n{issues}\n"
            f"This may indicate a bug in event handling. "
            f"Please report this issue with the conversation ID."
        )
        super().__init__(self.message)


def _index_tool_calls(
    events: Sequence[Event],
) -> tuple[dict[str, ActionEvent], set[str]]:
    """Build index of tool_call_ids from events.

    Returns:
        Tuple of (action_map, observation_ids) where:
        - action_map: tool_call_id -> ActionEvent
        - observation_ids: set of tool_call_ids that have observations
    """
    action_map: dict[str, ActionEvent] = {}
    observation_ids: set[str] = set()

    for event in events:
        if isinstance(event, ActionEvent) and event.tool_call_id:
            action_map[event.tool_call_id] = event
        elif (
            isinstance(
                event, (ObservationEvent, UserRejectObservation, AgentErrorEvent)
            )
            and event.tool_call_id
        ):
            observation_ids.add(event.tool_call_id)

    return action_map, observation_ids


def validate_event_stream(events: Sequence[Event]) -> list[str]:
    """Validate event stream invariants.

    Args:
        events: Sequence of events to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors: list[str] = []
    action_map, observation_ids = _index_tool_calls(events)

    # Check for duplicate observations (requires second pass)
    seen_obs: set[str] = set()
    for event in events:
        if (
            isinstance(
                event, (ObservationEvent, UserRejectObservation, AgentErrorEvent)
            )
            and event.tool_call_id
        ):
            if event.tool_call_id in seen_obs:
                errors.append(
                    f"Duplicate observation tool_call_id: {event.tool_call_id}"
                )
            seen_obs.add(event.tool_call_id)

    # Check for orphan actions (no observation)
    for tc_id in set(action_map.keys()) - observation_ids:
        errors.append(f"Orphan action (no observation): {tc_id}")

    # Check for orphan observations (no action)
    for tc_id in observation_ids - set(action_map.keys()):
        errors.append(f"Orphan observation (no action): {tc_id}")

    return errors


def validate_for_llm(events: Sequence[Event]) -> None:
    """Validate event stream before sending to LLM.

    Call this before events_to_messages() to catch issues early with
    clear error messages that can be displayed in the frontend.

    Args:
        events: Sequence of events to validate

    Raises:
        EventStreamValidationError: If validation fails with details
    """
    errors = validate_event_stream(events)
    if errors:
        raise EventStreamValidationError(errors)


def get_repair_events(events: Sequence[Event]) -> list[AgentErrorEvent]:
    """Get synthetic events to persist for orphan actions.

    For orphan actions (tool calls without observations), creates synthetic
    AgentErrorEvent to inform the LLM the tool execution was interrupted.

    This is the ONLY safe repair: adding events. Called on conversation
    resume; events should be persisted to the event store.

    Other issues (duplicate observations, orphan observations) are NOT
    repaired here - they indicate bugs that need investigation.

    Args:
        events: Sequence of events to analyze

    Returns:
        List of AgentErrorEvent to append to the event store.
        Empty list if no orphan actions found.
    """
    action_map, observation_ids = _index_tool_calls(events)
    orphan_ids = set(action_map.keys()) - observation_ids

    synthetic_events: list[AgentErrorEvent] = []
    for tc_id in orphan_ids:
        action = action_map[tc_id]
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
