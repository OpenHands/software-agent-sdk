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

    def _is_tool_loop_event(self, event: Event) -> bool:
        """Utility function for capturing the kinds of events that comprise tool loops.

        Says nothing about whether the event is part of a tool loop, just if it is an
        action or observation that _can be_ part of a tool loop.

        Args:
            event: An event object.

        Returns:
            True if the event can form a tool loop, false otherwise.
        """
        match event:
            case ActionEvent():
                return True

            # Observation base events also capture tool use errors.
            case ObservationBaseEvent():
                return True

            # The fall-through case -- anything not identifiable as able to comprise a
            # tool loop is _not_ a tool loop event.
            case _:
                return False

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
            is_tool_loop_event = self._is_tool_loop_event(event)
            in_tool_loop = current_tool_loop is not None

            match (in_tool_loop, is_tool_loop_event):
                # We're not in a tool loop and aren't entering one.
                case (False, False):
                    continue

                # We're just entering a tool loop. Start tracking a new tool loop with
                # the current event ID.
                case (False, True):
                    current_tool_loop = set(event.id)

                # We're stuck in a tool loop. Add the event ID and keep going.
                case (True, True):
                    assert current_tool_loop is not None
                    current_tool_loop.add(event.id)

                # We're exiting a tool loop. Move the current tool loop to the output
                # list and clear it.
                case (True, False):
                    assert current_tool_loop is not None
                    tool_loops.append(current_tool_loop)
                    current_tool_loop = None

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

        events_to_remove: set[EventID] = set()

        for view_tool_loop in self._tool_loops(current_view_events):
            # We assume the current view events (or at least the ones that make up tool
            # loops) are a subset of all the events. If a tool loop in the view isn't
            # present in the total list of tool loops that indicates some element has
            # been forgotten and we have to remove the remaining elements from the view.
            if view_tool_loop not in all_tool_loops:
                events_to_remove.update(view_tool_loop)

        return events_to_remove

    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],  # noqa: ARG002
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
            is_tool_loop_event = self._is_tool_loop_event(event)

            # There are four main cases:
            match (in_tool_loop, is_tool_loop_event):
                # We're not in a tool loop and aren't entering one. Keep the enumeration
                # index in the manipulation indices and keep going.
                case (False, False):
                    continue

                # We're just entering a tool loop. In this case we keep the enumeration
                # index in the manipulation indices to be able to manipulate the start
                # of the tool loop.
                case (False, True):
                    in_tool_loop = True

                # We're stuck in a tool loop. Remove the enumeration index from the
                # manipulation indices to avoid splitting the tool loop in the future.
                case (True, True):
                    manipulation_indices.remove(index)

                # We're exiting a tool loop. Keep the enumeration index in the
                # manipulation indices so we can reference the end of the tool loop
                # during condensation.
                case (True, False):
                    in_tool_loop = False

        return manipulation_indices
