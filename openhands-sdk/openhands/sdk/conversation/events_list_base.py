from abc import ABC, abstractmethod
from collections.abc import Sequence

from openhands.sdk.event import Event
from openhands.sdk.event.types import EventID


class EventsListBase(Sequence[Event], ABC):
    """Abstract base class for event lists that can be appended to.

    This provides a common interface for both local EventLog and remote
    RemoteEventsList implementations, avoiding circular imports in protocols.
    """

    @abstractmethod
    def append(self, event: Event) -> None:
        """Add a new event to the list."""
        ...

    @abstractmethod
    def get_index(self, event_id: EventID) -> int:
        """Return the integer index for a given event_id."""
        ...
