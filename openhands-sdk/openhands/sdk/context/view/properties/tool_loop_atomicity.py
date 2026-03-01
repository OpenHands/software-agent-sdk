from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.base import ViewPropertyBase
from openhands.sdk.event import (
    ActionEvent,
    Event,
    EventID,
    LLMConvertibleEvent,
    ObservationBaseEvent,
)


class ToolLoopAtomicityProperty(ViewPropertyBase):
    """A tool loop is a sequence of action/observation pairs, with nothing in between,
    that some agents identify as a single turn.

    This property is important to enforce for Anthropic models with thinking enabled.
    They expect the first element of such a tool loop to have a thinking block, and use
    some checksums to make sure it is correctly placed. In such a setup if we remove any
    element of the tool loop we have to remove the whole thing.
    """

    def _tool_loops(self, events: Sequence[Event]) -> list[set[EventID]]:
        """Calculate all tool loops in the events.

        Args:
            events: A sequence of events. Must be in-order.

        Returns:
            A list of tool loops, each represented by a set of IDs corresponding to the
            events in the loop.
        """
        tool_loops: list[set[EventID]] = []
        current_tool_loop: set[EventID] | None = None

        for event in events:
            match event:
                # We start a tool loop if we find an action event with thinking blocks.
                # If a tool loop already exists, end it and start a new one.
                case ActionEvent() if event.thinking_blocks:
                    if current_tool_loop is not None:
                        tool_loops.append(current_tool_loop)
                    current_tool_loop = {event.id}

                # If we see actions or observations, the current tool loop status stays
                # the same -- if we're in a tool loop, the event is part of it, and if
                # we're not in a tool loop we don't start one.
                case ActionEvent() | ObservationBaseEvent():
                    if current_tool_loop is not None:
                        current_tool_loop.add(event.id)

                # In all other situations we exit a tool loop.
                case _:
                    if current_tool_loop is not None:
                        tool_loops.append(current_tool_loop)
                        current_tool_loop = None

        # If the events end while we're still in a tool loop, append it to the output.
        if current_tool_loop is not None:
            tool_loops.append(current_tool_loop)

        return tool_loops

    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],
    ) -> set[EventID]:
        """Enforce tool loop atomicity by removing partially-present tool loops.

        Requires we iterate over all events to determine the full extent of tool loops.
        """
        all_tool_loops: list[set[EventID]] = self._tool_loops(all_events)
        view_event_ids: set[EventID] = {event.id for event in current_view_events}
        events_to_remove: set[EventID] = set()

        for event in current_view_events:
            # If the event is already marked for removal, we can skip the subsequent
            # checks.
            if event.id in events_to_remove:
                continue

            # Check if the event is part of a tool loop. If it is, all events in that
            # tool loop must be part of the view or we have to remove the remaining
            # events.
            for tool_loop in all_tool_loops:
                if event.id in tool_loop:
                    if not tool_loop.issubset(view_event_ids):
                        events_to_remove.update(view_event_ids & tool_loop)
                    break

        return events_to_remove

    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
    ) -> ManipulationIndices:
        """Calculate manipulation indices that respect tool loop atomicity.

        All indices that lie within a tool loop are removed.
        """
        manipulation_indices: ManipulationIndices = ManipulationIndices.complete(
            current_view_events
        )

        # To identify the boundaries of the tool loops, we must step through all events
        # in order and keep track of whether we're in a tool loop or not. Based on when
        # we enter and exit the tool loops we can remove events from the manipulation
        # indices (or not) to ensure all manipulation indices are at the boundaries of
        # tool loops.
        in_tool_loop: bool = False

        for index, event in enumerate(current_view_events):
            match event:
                case ActionEvent() if event.thinking_blocks:
                    in_tool_loop = True

                case ActionEvent() | ObservationBaseEvent():
                    if in_tool_loop:
                        manipulation_indices.remove(index)

                case _:
                    in_tool_loop = False

        return manipulation_indices
