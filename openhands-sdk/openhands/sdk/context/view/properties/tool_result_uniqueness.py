"""Property to ensure each tool_call_id has exactly one tool result.

LLM APIs (especially Anthropic) require that each tool_use has exactly one
corresponding tool_result. This property handles the edge case where multiple
observation events (e.g., AgentErrorEvent and ObservationEvent) are created
for the same tool_call_id, typically due to restarts or race conditions.

When both AgentErrorEvent and ObservationEvent exist for the same tool_call_id,
the error context is merged into the observation to preserve both pieces of
information for the LLM.

IMPORTANT: This property only handles CONSECUTIVE duplicate observations.
If two observations with the same tool_call_id are separated by other events,
this indicates a more serious bug that should be exposed rather than hidden.
"""

import uuid
from collections import defaultdict
from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event import (
    AgentErrorEvent,
    Event,
    EventID,
    LLMConvertibleEvent,
    ObservationBaseEvent,
    ObservationEvent,
    ToolCallID,
)
from openhands.sdk.llm import ImageContent, TextContent


def _create_merged_observation(
    obs_event: ObservationEvent,
    error_events: list[AgentErrorEvent],
) -> ObservationEvent:
    """Create a new ObservationEvent with error context merged into the observation.

    The error messages from AgentErrorEvents are prepended to the observation content,
    giving the LLM context about any issues that occurred during tool execution.

    Args:
        obs_event: The original ObservationEvent with the actual tool result.
        error_events: List of AgentErrorEvents to merge (typically from restarts).

    Returns:
        A new ObservationEvent with merged content and a new unique ID.
    """
    # Collect error messages
    error_texts = [f"[Note: {error.error}]" for error in error_events]
    error_prefix = "\n".join(error_texts) + "\n\n"

    # Create new content list with error context prepended
    original_content = list(obs_event.observation.content)
    merged_content: list[TextContent | ImageContent] = [TextContent(text=error_prefix)]
    merged_content.extend(original_content)

    # Create a new observation with merged content using model_copy
    merged_observation = obs_event.observation.model_copy(
        update={"content": merged_content}
    )

    # Create new ObservationEvent with a unique ID
    # ID format: "{original_id}-merged-{uuid}" to ensure uniqueness
    return ObservationEvent(
        id=f"{obs_event.id}-merged-{uuid.uuid4().hex[:8]}",
        tool_name=obs_event.tool_name,
        tool_call_id=obs_event.tool_call_id,
        observation=merged_observation,
        action_id=obs_event.action_id,
        source=obs_event.source,
    )


def _group_consecutive_observations_by_tool_call(
    events: list[LLMConvertibleEvent],
) -> dict[ToolCallID, list[ObservationBaseEvent]]:
    """Group CONSECUTIVE observations by their tool_call_id.

    Only observations that are consecutive in the event list are grouped together.
    If two observations with the same tool_call_id are separated by other events,
    they are NOT grouped (indicating a bug that should be exposed).

    Args:
        events: The list of events to process.

    Returns:
        A mapping from tool_call_id to list of consecutive observations with that ID.
        Only includes tool_call_ids that have consecutive duplicate observations.
    """
    consecutive_groups: dict[ToolCallID, list[ObservationBaseEvent]] = defaultdict(list)

    # Track the last tool_call_id and its observations for consecutive detection
    last_tool_call_id: ToolCallID | None = None
    current_group: list[ObservationBaseEvent] = []

    for event in events:
        if isinstance(event, ObservationBaseEvent):
            if last_tool_call_id == event.tool_call_id:
                # Consecutive observation with same tool_call_id
                current_group.append(event)
            else:
                # New tool_call_id - save previous group if it had duplicates
                if len(current_group) > 1:
                    assert last_tool_call_id is not None
                    consecutive_groups[last_tool_call_id] = current_group
                # Start new group
                last_tool_call_id = event.tool_call_id
                current_group = [event]
        else:
            # Non-observation event - save previous group if it had duplicates
            if len(current_group) > 1:
                assert last_tool_call_id is not None
                consecutive_groups[last_tool_call_id] = current_group
            # Reset tracking
            last_tool_call_id = None
            current_group = []

    # Don't forget the last group
    if len(current_group) > 1:
        assert last_tool_call_id is not None
        consecutive_groups[last_tool_call_id] = current_group

    return consecutive_groups


class ToolResultUniquenessProperty(ViewPropertyBase):
    """Each tool_call_id must have exactly one tool result.

    When multiple CONSECUTIVE observations exist for the same tool_call_id,
    this property:
    1. Merges AgentErrorEvent content into ObservationEvent (if both exist)
    2. Keeps only the merged/primary event and removes duplicates
    3. Prefers ObservationEvent > other observations > AgentErrorEvent

    IMPORTANT: Only consecutive duplicates are handled. If two observations
    with the same tool_call_id are separated by other events, this property
    does NOT merge them - the underlying bug should be exposed rather than hidden.
    """

    def transform(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> dict[EventID, LLMConvertibleEvent]:
        """Merge AgentErrorEvent content into ObservationEvent when both exist.

        When an AgentErrorEvent and ObservationEvent share the same tool_call_id
        AND are consecutive (typically from a restart scenario), merge the error
        context into the observation so the LLM has full context about what happened.

        Non-consecutive duplicates are NOT merged - they indicate a bug.
        """
        consecutive_groups = _group_consecutive_observations_by_tool_call(
            current_view_events
        )
        transforms: dict[EventID, LLMConvertibleEvent] = {}

        for observations in consecutive_groups.values():
            # Find ObservationEvents and AgentErrorEvents
            obs_events = [o for o in observations if isinstance(o, ObservationEvent)]
            error_events = [o for o in observations if isinstance(o, AgentErrorEvent)]

            # Only merge if we have both an ObservationEvent and AgentErrorEvent(s)
            if obs_events and error_events:
                # Use the last ObservationEvent as the base
                base_obs = obs_events[-1]
                # Create merged observation with error context
                merged_event = _create_merged_observation(base_obs, error_events)
                transforms[base_obs.id] = merged_event

        return transforms

    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> set[EventID]:
        """Remove duplicate tool results for the same tool_call_id.

        After transform() has merged error context into observations, this method
        removes the remaining duplicate events (the original AgentErrorEvents and
        any other duplicates).

        Only handles CONSECUTIVE duplicates - non-consecutive duplicates are NOT
        removed to expose the underlying bug.
        """
        consecutive_groups = _group_consecutive_observations_by_tool_call(
            current_view_events
        )
        events_to_remove: set[EventID] = set()

        for observations in consecutive_groups.values():
            # Multiple consecutive observations for same tool_call_id - need to pick one
            # Priority: ObservationEvent > other observations > AgentErrorEvent
            # If same priority, keep the later one (more recent)
            obs_events = [o for o in observations if isinstance(o, ObservationEvent)]
            error_events = [o for o in observations if isinstance(o, AgentErrorEvent)]
            other_events = [
                o
                for o in observations
                if not isinstance(o, (ObservationEvent, AgentErrorEvent))
            ]

            # Determine which one to keep
            if obs_events:
                # Keep the last ObservationEvent, remove all others
                to_keep = obs_events[-1]
            elif other_events:
                # Keep the last "other" event (UserRejectObservation, etc.)
                to_keep = other_events[-1]
            else:
                # Only AgentErrorEvents, keep the last one
                to_keep = error_events[-1]

            # Mark all others for removal
            for obs in observations:
                if obs.id != to_keep.id:
                    events_to_remove.add(obs.id)

        return events_to_remove

    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
    ) -> ManipulationIndices:
        """Calculate manipulation indices for tool result uniqueness.

        This property doesn't restrict manipulation - it only enforces
        uniqueness when violations are detected.
        """
        return ManipulationIndices.complete(current_view_events)
