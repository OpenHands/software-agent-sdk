from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import ValidationError

from openhands.sdk.event import Event
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# A stored event this process cannot materialize: its ``kind`` is not registered
# (a custom tool's observation whose module was never imported, or an event from
# a newer writer), its file is missing, or its bytes are unusable. Reading one is
# recoverable per event; it must not fail the whole conversation (#4080).
UNREADABLE_EVENT_ERRORS = (FileNotFoundError, UnicodeDecodeError, ValidationError)


def read_event_or_none(events: Sequence[Event], index: int) -> Event | None:
    """The event at ``index``, or ``None`` if it cannot be deserialized.

    For callers that would rather drop one event than fail the whole read.
    """
    try:
        return events[index]
    except UNREADABLE_EVENT_ERRORS as exc:
        logger.warning(
            "Skipping unreadable event at index %d (%s): %s",
            index,
            type(exc).__name__,
            exc,
        )
        return None


def readable_events(events: Sequence[Event]) -> list[Event]:
    """Every event in ``events`` that deserializes, skipping any that does not.

    Read by index, so one unreadable event costs only itself. For callers that
    scan a whole log and must not fail it over a single event (#4080).
    """
    return [
        event
        for index in range(len(events))
        if (event := read_event_or_none(events, index)) is not None
    ]


class EventsListBase(Sequence[Event], ABC):
    """Abstract base class for event lists that can be appended to.

    This provides a common interface for both local EventLog and remote
    RemoteEventsList implementations, avoiding circular imports in protocols.
    """

    @abstractmethod
    def append(self, event: Event) -> None:
        """Add a new event to the list."""
        ...
