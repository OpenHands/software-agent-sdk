from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.event.base import Event, LLMConvertibleEvent
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.event.types import EventID


class ViewPropertyBase(ABC):
    """Abstract base class for properties of a view.

    Properties define rules that help maintain the integrity and coherence of the events
    in the view. The properties are maintained via two strategies:

    1. Enforcing the property by removing events that violate it.
    2. Defining manipulation indices that restrict where the view can be manipulated.

    In an ideal scenario, sticking to the manipulation indices should suffice to ensure
    the property holds. Enforcement is only intended as a fallback mechanism to handle
    edge cases, bad data, or unforeseen situations.
    """

    @abstractmethod
    def enforce(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> set[EventID]:
        """Enforce the property on a list of events.

        Args:
            current_view_events: A list of events currently in the view.
            all_events: A list of all Event objects in the conversation. Useful for
                properties that need to reference events outside the current view.

        Returns:
            A set of EventID objects to be removed from the current view to enforce the
            property.
        """
        pass

    @abstractmethod
    def manipulation_indices(
        self, current_view_events: list[LLMConvertibleEvent], all_events: list[Event]
    ) -> ManipulationIndices:
        """Get manipulation indices for the property on a list of events.

        Args:
            current_view_events: A list of events currently in the view.
            all_events: A list of all Event objects in the conversation. Useful for
                properties that need to reference events outside the current view.

        Returns:
            A ManipulationIndices object defining where the view can be manipulated
            while maintaining the property.
        """
        pass

    @staticmethod
    def _build_batches(events: Sequence[Event]) -> dict[EventID, list[EventID]]:
        """Build mapping of llm_response_id to ActionEvent IDs.

        Args:
            events: Sequence of events to analyze

        Returns:
            Dictionary mapping llm_response_id to list of ActionEvent IDs
        """
        batches: dict[EventID, list[EventID]] = defaultdict(list)
        for event in events:
            if isinstance(event, ActionEvent):
                batches[event.llm_response_id].append(event.id)
        return dict(batches)

    @staticmethod
    def _build_event_id_to_index(events: Sequence[Event]) -> dict[EventID, int]:
        """Build mapping of event ID to index.

        Args:
            events: Sequence of events to analyze

        Returns:
            Dictionary mapping event ID to index in the list
        """
        return {event.id: idx for idx, event in enumerate(events)}

    @staticmethod
    def _get_batch_extent(
        action_ids: list[EventID],
        event_id_to_index: dict[EventID, int],
    ) -> tuple[int, int]:
        """Get the min and max indices for a batch of action IDs.

        Args:
            action_ids: List of ActionEvent IDs in the batch
            event_id_to_index: Mapping of event IDs to indices

        Returns:
            Tuple of (min_index, max_index) for the batch
        """
        indices = [event_id_to_index[aid] for aid in action_ids]
        return min(indices), max(indices)

    @staticmethod
    def _build_manipulation_indices_from_atomic_ranges(
        atomic_ranges: list[tuple[int, int]],
        num_events: int,
    ) -> ManipulationIndices:
        """Build ManipulationIndices that exclude indices within atomic ranges.

        Atomic ranges represent contiguous sequences of events that must remain together.
        This method creates a set of valid manipulation indices that excludes all indices
        that fall within these atomic ranges.

        Args:
            atomic_ranges: List of (start_idx, end_idx) tuples defining atomic ranges
            num_events: Total number of events in the view

        Returns:
            ManipulationIndices with valid indices (excluding those within atomic ranges)
        """
        # Start with all possible indices (including after the last event)
        valid_indices = set(range(num_events + 1))

        # Remove indices that fall within atomic ranges
        for start_idx, end_idx in atomic_ranges:
            # Cannot insert/remove within the atomic range (exclusive of start boundary)
            for idx in range(start_idx + 1, end_idx + 1):
                valid_indices.discard(idx)

        return ManipulationIndices(valid_indices)
