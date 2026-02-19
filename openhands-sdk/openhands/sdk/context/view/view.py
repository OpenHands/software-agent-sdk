from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from logging import getLogger
from typing import overload

from pydantic import BaseModel

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties import ALL_PROPERTIES
from openhands.sdk.event import (
    Condensation,
    CondensationRequest,
    LLMConvertibleEvent,
)
from openhands.sdk.event.base import Event


logger = getLogger(__name__)


class View(BaseModel):
    """Linearly ordered view of events.

    Produced by a condenser to indicate the included events are ready to process as LLM
    input. Also contains fields with information from the condensation process to aid
    in deciding whether further condensation is needed.
    """

    events: list[LLMConvertibleEvent]

    unhandled_condensation_request: bool = False
    """Whether there is an unhandled condensation request in the view."""

    def __len__(self) -> int:
        return len(self.events)

    @cached_property
    def manipulation_indices(self) -> ManipulationIndices:
        """The indices where the view events can be manipulated without violating the
        properties expected by LLM APIs.

        Each property generates an independent set of manipulation indices. An index is
        in the returned set of manipulation indices if it exists in _all_ the sets of
        property-derived indices.
        """
        results: ManipulationIndices = ManipulationIndices.complete(self.events)
        for property in ALL_PROPERTIES:
            results &= property.manipulation_indices(self.events)
        return results

    # To preserve list-like indexing, we ideally support slicing and position-based
    # indexing. The only challenge with that is switching the return type based on the
    # input type -- we can mark the different signatures for MyPy with `@overload`
    # decorators.

    @overload
    def __getitem__(self, key: slice) -> list[LLMConvertibleEvent]: ...

    @overload
    def __getitem__(self, key: int) -> LLMConvertibleEvent: ...

    def __getitem__(
        self, key: int | slice
    ) -> LLMConvertibleEvent | list[LLMConvertibleEvent]:
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return [self[i] for i in range(start, stop, step)]
        elif isinstance(key, int):
            return self.events[key]
        else:
            raise ValueError(f"Invalid key type: {type(key)}")

    @staticmethod
    def unhandled_condensation_request_exists(
        events: Sequence[Event],
    ) -> bool:
        """Check if there is an unhandled condensation request in the list of events.

        An unhandled condensation request is defined as a CondensationRequest event
        that appears after the most recent Condensation event in the list.
        """
        for event in reversed(events):
            if isinstance(event, Condensation):
                return False
            if isinstance(event, CondensationRequest):
                return True
        return False

    def enforce_properties(self, all_events: Sequence[Event]) -> None:
        """Enforce all properties on the list of current view events.

        Repeatedly applies each property's enforcement mechanism until the list of view
        events reaches a stable state.

        Since enforcement is intended as a fallback to inductively maintaining the
        properties via the associated manipulation indices, any time a property must be
        enforced a warning is logged.

        Modifies the view in-place.
        """
        for property in ALL_PROPERTIES:
            events_to_forget = property.enforce(self.events, all_events)
            if events_to_forget:
                logger.warning(
                    f"Property {property.__class__} enforced, "
                    f"{len(events_to_forget)} events dropped."
                )
                self.events = [
                    event for event in self.events if event.id not in events_to_forget
                ]

                # If we've forgotten events to enforce the properties, we'll need to
                # attempt to apply each property again. Once we get all the way through
                # the properties without any kind of modification, we can exit the loop.
                self.enforce_properties(all_events)
                break

    def add_event(self, event: Event) -> None:
        """Add an event to the view.

        Updates the view in-place.

        LLMConvertibleEvent objects are appended to the end of the view's events.
        Condensation-related events will update the unhandled_condensation_request flag
        and apply any condensations to the view's events by forgetting marked events and
        inserting summaries.

        Condensation semantics assume events are added in chronological order, as they
        are produced by the LLM.

        Args:
            event: An event to add to the view.
        """
        match event:
            case CondensationRequest():
                self.unhandled_condensation_request = True

            # By the time we come across a Condensation event, the events list should
            # already reflect the events seen by the agent up to that point. We can
            # therefore apply the condensation semantics directly to the view's events.
            case Condensation():
                self.unhandled_condensation_request = False
                self.events = event.apply(self.events)

            case LLMConvertibleEvent():
                self.events.append(event)

            # If the event isn't related to condensation and isn't LLMConvertible, it
            # should not be in the resulting view. Examples include certain internal
            # events used for state tracking that the LLM does not need to see -- see,
            # for example, ConversationStateUpdateEvent, PauseEvent, and (relevant here)
            # CondensationRequest.
            case _:
                logger.debug(f"Skipping non-LLMConvertibleEvent of type {type(event)}")

    @staticmethod
    def from_events(events: Sequence[Event]) -> View:
        """Create a view from a list of events, respecting the semantics of any
        condensation events.
        """
        output: View = View(events=[])

        for event in events:
            output.add_event(event)

        output.enforce_properties(events)

        return output
