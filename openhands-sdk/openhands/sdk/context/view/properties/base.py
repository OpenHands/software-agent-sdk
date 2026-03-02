from abc import ABC, abstractmethod
from collections.abc import Sequence

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.event import Event, EventID, LLMConvertibleEvent


class ViewPropertyBase(ABC):
    """Abstract base class for properties of a view.

    Properties define rules that help maintain the integrity and coherence of the events
    in the view. The properties are maintained by two strategies:

    1. Enforcing the property by removing events that violate it.
    2. Defining manipulation indices that restrict where the view can be modified.

    The main way views are manipulated (beyond adding new events in the course of a
    conversation) is in the condensers, which are designed to respect the manipulation
    indices. That means properties should hold inductively, and manipulation indices
    should be calculable purely from the events in the current view.

    Enforcement is intended as a fallback mechanism to handle edge cases, bad data, or
    unforeseen situations. Because enforcement assumes the view is in a bad state, it
    often requires a much larger perspective on the events and therefore depends on a
    sequence of _all_ events in the conversation.
    """

    @abstractmethod
    def enforce(
        self,
        current_view_events: list[LLMConvertibleEvent],
        all_events: Sequence[Event],
    ) -> set[EventID]:
        """Enforce the property on a list of events.

        Args:
            current_view_events: The sequence of events currently in the view.
            all_events: A list of all Event objects in the conversation. Useful for
                properties that need to reference events outside the current view.

        Returns:
            A set of EventID objects corresponding to events that should be removed from
            the current view to enforce the property.
        """

    def transform(
        self,
        current_view_events: list[LLMConvertibleEvent],  # noqa: ARG002
        all_events: Sequence[Event],  # noqa: ARG002
    ) -> dict[EventID, LLMConvertibleEvent]:
        """Transform events in the view by replacing them with modified versions.

        This method allows properties to merge or modify events rather than just
        removing them. The default implementation returns an empty dict (no transforms).

        Args:
            current_view_events: The sequence of events currently in the view.
            all_events: A list of all Event objects in the conversation.

        Returns:
            A mapping from original EventID to the replacement LLMConvertibleEvent.
            Events whose IDs appear as keys will be replaced with the corresponding
            values. The replacement events should have new unique IDs.
        """
        return {}

    @abstractmethod
    def manipulation_indices(
        self,
        current_view_events: list[LLMConvertibleEvent],
    ) -> ManipulationIndices:
        """Get manipulation indices for the property on a list of events.

        Args:
            current_view_events: The sequence of events currently in the view.

        Returns:
            A ManipulationIndices object defining where the view can be modified while
            maintaining the property.
        """
