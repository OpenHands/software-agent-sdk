"""Property for ensuring ActionEvents and ObservationEvents are properly paired."""

from openhands.sdk.context.view.event_mappings import EventMappings
from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.llm_convertible.observation import ObservationBaseEvent
from openhands.sdk.event.types import EventID


class ToolCallMatchingProperty(ViewPropertyBase):
    """Ensures ActionEvents and ObservationEvents are properly paired via tool_call_id.

    LLM APIs expect tool calls to have corresponding observations. Orphaned actions
    or observations cause API errors.
    """

    def enforce(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> set[EventID]:
        """Enforce tool call matching by removing orphaned actions and observations.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            Set of EventIDs to remove from the current view
        """
        mappings = EventMappings.from_events(current_view_events)

        events_to_remove: set[EventID] = set()

        # Remove ActionEvents without matching observations
        for event in current_view_events:
            if isinstance(event, ActionEvent):
                if event.tool_call_id not in mappings.observation_tool_call_ids:
                    events_to_remove.add(event.id)

            # Remove ObservationEvents without matching actions
            elif isinstance(event, ObservationBaseEvent):
                if event.tool_call_id not in mappings.action_tool_call_ids:
                    events_to_remove.add(event.id)

        return events_to_remove

    def manipulation_indices(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> ManipulationIndices:
        """Calculate manipulation indices for tool call matching.

        All indices are valid for this property. Validation happens through
        filtering in the enforce method, not through boundary restriction.

        Args:
            current_view_events: Events currently in the view
            all_events: All events in the conversation

        Returns:
            ManipulationIndices with all indices valid
        """
        # All indices are valid - filtering is done via enforce()
        return ManipulationIndices(set(range(len(current_view_events) + 1)))
