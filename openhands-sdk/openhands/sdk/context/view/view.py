from __future__ import annotations

from collections.abc import Sequence
from logging import getLogger
from typing import overload

from pydantic import BaseModel, Field

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.context.view.properties.batch_atomicity import (
    BatchAtomicityProperty,
)
from openhands.sdk.context.view.properties.tool_call_matching import (
    ToolCallMatchingProperty,
)
from openhands.sdk.context.view.properties.tool_loop_atomicity import (
    ToolLoopAtomicityProperty,
)
from openhands.sdk.event import (
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
    LLMConvertibleEvent,
)
from openhands.sdk.event.base import Event, EventID
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    ObservationBaseEvent,
)
from openhands.sdk.event.types import ToolCallID


logger = getLogger(__name__)


class View(BaseModel):
    """Linearly ordered view of events.

    Produced by a condenser to indicate the included events are ready to process as LLM
    input. Also contains fields with information from the condensation process to aid
    in deciding whether further condensation is needed.
    """

    model_config = {"arbitrary_types_allowed": True}

    events: list[LLMConvertibleEvent]

    unhandled_condensation_request: bool = False
    """Whether there is an unhandled condensation request in the view."""

    condensations: list[Condensation] = []
    """A list of condensations that were processed to produce the view."""

    def __len__(self) -> int:
        return len(self.events)

    @property
    def most_recent_condensation(self) -> Condensation | None:
        """Return the most recent condensation, or None if no condensations exist."""
        return self.condensations[-1] if self.condensations else None

    @property
    def summary_event_index(self) -> int | None:
        """Return the index of the summary event, or None if no summary exists."""
        recent_condensation = self.most_recent_condensation
        if (
            recent_condensation is not None
            and recent_condensation.summary is not None
            and recent_condensation.summary_offset is not None
        ):
            return recent_condensation.summary_offset
        return None

    @property
    def summary_event(self) -> CondensationSummaryEvent | None:
        """Return the summary event, or None if no summary exists."""
        if self.summary_event_index is not None:
            event = self.events[self.summary_event_index]
            if isinstance(event, CondensationSummaryEvent):
                return event
        return None

    manipulation_indices: ManipulationIndices = Field(
        description=(
            "Manipulation indices for this view's events. "
            "These indices represent boundaries between atomic units where events can be "
            "safely manipulated (inserted or forgotten). An atomic unit is either: "
            "a tool loop (sequence of batches starting with thinking blocks), "
            "a batch of ActionEvents with the same llm_response_id, or "
            "a single event that is neither an ActionEvent nor an ObservationBaseEvent. "
            "Always includes 0 and len(events) as boundaries."
        )
    )

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

    def find_next_manipulation_index(self, threshold: int, strict: bool = False) -> int:
        """Find the smallest manipulation index greater than (or equal to) a threshold.

        This is a helper method for condensation logic that needs to find safe
        boundaries for forgetting events. Uses the cached manipulation_indices property.

        Args:
            threshold: The threshold value to compare against
            strict: If True, finds index > threshold. If False, finds index >= threshold

        Returns:
            The smallest manipulation index that satisfies the condition, or the
            threshold itself if no such index exists
        """
        return self.manipulation_indices.find_next(threshold, strict)

    @staticmethod
    def from_events(events: Sequence[Event]) -> View:
        """Create a view from a list of events, respecting the semantics of any
        condensation events.
        """
        forgotten_event_ids: set[EventID] = set()
        condensations: list[Condensation] = []
        for event in events:
            if isinstance(event, Condensation):
                condensations.append(event)
                forgotten_event_ids.update(event.forgotten_event_ids)
                # Make sure we also forget the condensation action itself
                forgotten_event_ids.add(event.id)
            if isinstance(event, CondensationRequest):
                forgotten_event_ids.add(event.id)

        kept_events = [
            event
            for event in events
            if event.id not in forgotten_event_ids
            and isinstance(event, LLMConvertibleEvent)
        ]

        # If we have a summary, insert it at the specified offset.
        summary: str | None = None
        summary_offset: int | None = None

        # The relevant summary is always in the last condensation event (i.e., the most
        # recent one).
        for event in reversed(events):
            if isinstance(event, Condensation):
                if event.summary is not None and event.summary_offset is not None:
                    summary = event.summary
                    summary_offset = event.summary_offset
                    break

        if summary is not None and summary_offset is not None:
            logger.debug(f"Inserting summary at offset {summary_offset}")

            _new_summary_event = CondensationSummaryEvent(summary=summary)
            kept_events.insert(summary_offset, _new_summary_event)

        # Check for an unhandled condensation request -- these are events closer to the
        # end of the list than any condensation action.
        unhandled_condensation_request = False

        for event in reversed(events):
            if isinstance(event, Condensation):
                break

            if isinstance(event, CondensationRequest):
                unhandled_condensation_request = True
                break

        # Apply property enforcement iteratively
        # Properties are checked in order, and we restart from the first property
        # whenever any property removes events (to handle cascading effects)
        tool_call_matching = ToolCallMatchingProperty()
        batch_atomicity = BatchAtomicityProperty()
        tool_loop_atomicity = ToolLoopAtomicityProperty()

        properties = [
            tool_call_matching,  # Match actions/observations first
            batch_atomicity,  # Then ensure batch atomicity
            tool_loop_atomicity,  # Finally ensure tool loop atomicity
        ]

        view_events = kept_events
        max_iterations = 10  # Safety limit to prevent infinite loops

        for iteration in range(max_iterations):
            events_removed_this_iteration: set[EventID] = set()

            for prop in properties:
                events_to_remove = prop.enforce(view_events, events)
                if events_to_remove:
                    logger.debug(
                        f"Iteration {iteration + 1}: {prop.__class__.__name__} "
                        f"removing {len(events_to_remove)} events"
                    )
                    events_removed_this_iteration.update(events_to_remove)
                    # Exit inner loop and restart from first property
                    break

            if not events_removed_this_iteration:
                # No events removed by any property - enforcement complete
                break

            # Remove events and continue iterating
            view_events = [
                e for e in view_events if e.id not in events_removed_this_iteration
            ]
        else:
            # Hit max_iterations - log warning
            logger.warning(
                f"Property enforcement loop reached max iterations ({max_iterations}). "
                f"This may indicate cascading enforcement issues."
            )

        # Calculate manipulation_indices using the same property instances
        # For empty views, return {0} as the single manipulation index
        if not view_events:
            final_indices = ManipulationIndices({0})
        else:
            batch_indices = batch_atomicity.manipulation_indices(view_events, events)
            tool_loop_indices = tool_loop_atomicity.manipulation_indices(
                view_events, events
            )
            tool_call_indices = tool_call_matching.manipulation_indices(
                view_events, events
            )

            # Take the intersection of all three sets
            final_indices = ManipulationIndices(
                batch_indices & tool_loop_indices & tool_call_indices
            )

        return View(
            events=view_events,
            unhandled_condensation_request=unhandled_condensation_request,
            condensations=condensations,
            manipulation_indices=final_indices,
        )
