from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event import (
    ActionEvent,
    Event,
    EventID,
    LLMConvertibleEvent,
    ObservationBaseEvent,
    ToolCallID,
)


class ToolCallMatchingProperty(ViewPropertyBase):
    """Actions and observations must be paired."""

    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> set[EventID]:
        """Enforce tool-call matching by removing actions without matching observations,
        and vice versa.
        """
        # Start by collecting all tool call IDs associated with actions and observations
        # separately.
        action_tool_call_ids: set[ToolCallID] = set()
        observation_tool_call_ids: set[ToolCallID] = set()

        for event in current_view_events:
            match event:
                case ActionEvent():
                    action_tool_call_ids.add(event.tool_call_id)
                case ObservationBaseEvent():
                    observation_tool_call_ids.add(event.tool_call_id)

        # If an action event has a tool call ID that doesn't appear in any observation,
        # we need to remove it. Likewise, if an observation has a tool call ID that is
        # not in any action event, we need to remove it.
        events_to_remove: set[EventID] = set()

        for event in current_view_events:
            match event:
                case ActionEvent():
                    if event.tool_call_id not in observation_tool_call_ids:
                        events_to_remove.add(event.id)
                case ObservationBaseEvent():
                    if event.tool_call_id not in action_tool_call_ids:
                        events_to_remove.add(event.id)

        return events_to_remove

    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> ManipulationIndices:
        """Calculate manipulation indices for tool call matching.

        This property is maintained by ensuring there are no manipulation indices
        between action events and their paired observation event.
        """
        # Start with a complete set of manipulation indices, then we'll remove those
        # between actions and their paired observations.
        manipulation_indices: ManipulationIndices = ManipulationIndices.complete(
            current_view_events
        )

        # Actions always come before observations, so we can maintain a set of pending
        # tool calls -- these are any tool calls that have been introduced by an action
        # but not yet resolved by an observation. If there are any pending tool calls we
        # know we're between an action/observation pair.
        pending_tool_call_ids: set[ToolCallID] = set()

        for index, event in enumerate(current_view_events):
            match event:
                case ActionEvent():
                    pending_tool_call_ids.add(event.tool_call_id)
                case ObservationBaseEvent():
                    pending_tool_call_ids.remove(event.tool_call_id)

            if pending_tool_call_ids:
                # The enumeration index corresponds to the position of the event, but we
                # want the index just after.
                manipulation_indices.remove(index + 1)

        return manipulation_indices
