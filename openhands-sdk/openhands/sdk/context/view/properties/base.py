from abc import ABC, abstractmethod

from openhands.sdk.context.view.manipulation_indices import ManipulationIndices
from openhands.sdk.event.base import Event, LLMConvertibleEvent
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
