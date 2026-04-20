from __future__ import annotations

from collections.abc import Sequence
from logging import getLogger
from typing import overload

from pydantic import BaseModel, Field

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

    events: list[LLMConvertibleEvent] = Field(default_factory=list)

    unhandled_condensation_request: bool = False
    """Whether there is an unhandled condensation request in the view."""

    def __len__(self) -> int:
        return len(self.events)

    @property
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

    def enforce_properties(
        self,
        all_events: Sequence[Event],
    ) -> None:
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
                break

        # If we get all the way through the loop without hitting a break, that means no
        # properties needed to be enforced and we can keep the view as-is.
        else:
            return

        # If we did hit a break in the loop, a property applied and now we need to check
        # all the properties again to see if any are unblocked.
        self.enforce_properties(all_events)

    def append_event(self, event: Event) -> None:
        """Append an event to the end of the view, applying any condensation semantics
        as we do.

        Modifies the view in-place.
        """
        match event:
            # By the time we come across a Condensation event, the event list should
            # already reflect the events seen by the agent up to that point. We can
            # therefore apply the condensation semantics directly to the stored events.
            case Condensation():
                self.events = event.apply(self.events)
                self.unhandled_condensation_request = False

            case CondensationRequest():
                self.unhandled_condensation_request = True

            case LLMConvertibleEvent():
                self.events.append(event)

            # If the event isn't related to condensation and isn't LLMConvertible, it
            # should not be in the resulting view. Examples include certain internal
            # events used for state tracking that the LLM does not need to see -- see,
            # for example, ConversationStateUpdateEvent, PauseEvent, and (relevant here)
            # CondensationRequest.
            case _:
                logger.debug(
                    f"Skipping non-LLMConvertibleEvent of type {type(event)} "
                    "in View.append_event"
                )

    @staticmethod
    def from_events(events: Sequence[Event]) -> View:
        """Create a view from a list of events, respecting the semantics of any
        condensation events.
        """
        result: View = View()

        # Generate the LLMConvertibleEvent objects the agent can send to the LLM by
        # adding them one at a time to the result view. This ensures condensations are
        # applied in the order they were generated and condensation requests are
        # appropriately tracked.
        for event in events:
            result.append_event(event)

        # Once all the events are loaded enforce the relevant properties to ensure
        # the construction was done properly.
        result.enforce_properties(events)

        return result
