"""Property to ensure each tool_call_id has exactly one tool result.

LLM APIs (especially Anthropic) require that each tool_use has exactly one
corresponding tool_result. This property handles the edge case where multiple
observation events (e.g., AgentErrorEvent and ObservationEvent) are created
for the same tool_call_id, typically due to restarts or race conditions.
"""

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


class ToolResultUniquenessProperty(ViewPropertyBase):
    """Each tool_call_id must have exactly one tool result.

    When multiple observations exist for the same tool_call_id, this property
    keeps the most informative one:
    1. ObservationEvent (actual tool result) is preferred over AgentErrorEvent
    2. If multiple of the same type exist, the later one is kept
    """

    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> set[EventID]:
        """Remove duplicate tool results for the same tool_call_id.

        When multiple ObservationBaseEvents share the same tool_call_id,
        keep only one - preferring ObservationEvent over AgentErrorEvent.
        """
        # Group observations by tool_call_id
        observations_by_tool_call: dict[ToolCallID, list[ObservationBaseEvent]] = (
            defaultdict(list)
        )

        for event in current_view_events:
            if isinstance(event, ObservationBaseEvent):
                observations_by_tool_call[event.tool_call_id].append(event)

        events_to_remove: set[EventID] = set()

        for tool_call_id, observations in observations_by_tool_call.items():
            if len(observations) <= 1:
                continue

            # Multiple observations for same tool_call_id - need to pick one
            # Priority: ObservationEvent > AgentErrorEvent > others
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
