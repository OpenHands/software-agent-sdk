from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence

from openhands.sdk.event import Event


class EventsListBase(Sequence[Event], ABC):
    """Abstract base class for event lists that can be appended to.

    This provides a common interface for both local EventLog and remote
    RemoteEventsList implementations, avoiding circular imports in protocols.
    """

    def iter_events(self, start: int = 0, stop: int | None = None) -> Iterator[Event]:
        """Iterate through events without materializing the full log.

        Args:
            start: Inclusive start index.
            stop: Exclusive stop index. Defaults to the length of the list.
        """
        if stop is None:
            stop_index = len(self)
        else:
            stop_index = stop

        if start < 0 or stop_index < 0:
            start, stop_index, _ = slice(start, stop).indices(len(self))

        for idx in range(start, stop_index):
            yield self[idx]

    @abstractmethod
    def append(self, event: Event) -> None:
        """Add a new event to the list."""
        ...
