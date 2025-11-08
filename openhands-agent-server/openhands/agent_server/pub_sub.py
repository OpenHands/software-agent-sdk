import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar
from uuid import UUID, uuid4

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

T = TypeVar("T")


class Subscriber[T](ABC):
    @abstractmethod
    async def __call__(self, event: T):
        """Invoke this subscriber"""

    async def close(self):
        """Clean up this subscriber"""


@dataclass
class PubSub[T]:
    """A subscription service that extends ConversationCallbackType functionality.
    This class maintains a dictionary of UUIDs to ConversationCallbackType instances
    and provides methods to subscribe/unsubscribe callbacks. When invoked, it calls
    all registered callbacks with proper error handling.
    """

    _subscribers: dict[UUID, Subscriber[T]] = field(default_factory=dict)

    def subscribe(self, subscriber: Subscriber[T]) -> UUID:
        """Subscribe a subscriber and return its UUID for later unsubscription.
        Args:
            subscriber: The callback function to register
        Returns:
            UUID: UUID that can be used to unsubscribe this callback
        """
        subscriber_id = uuid4()
        self._subscribers[subscriber_id] = subscriber
        logger.debug(f"Subscribed subscriber with ID: {subscriber_id}")
        return subscriber_id

    def unsubscribe(self, subscriber_id: UUID) -> bool:
        """Unsubscribe a subscriber by its UUID.
        Args:
            subscriber_id: The UUID returned by subscribe()
        Returns:
            bool: True if subscriber was found and removed, False otherwise
        """
        if subscriber_id in self._subscribers:
            del self._subscribers[subscriber_id]
            logger.debug(f"Unsubscribed subscriber with ID: {subscriber_id}")
            return True
        else:
            logger.warning(
                f"Attempted to unsubscribe unknown subscriber ID: {subscriber_id}"
            )
            return False

    async def __call__(self, event: T) -> None:
        """Invoke all registered callbacks with the given event.
        Each callback is invoked in its own try/catch block to prevent
        one failing callback from affecting others.
        Args:
            event: The event to pass to all callbacks
        """
        for subscriber_id, subscriber in list(self._subscribers.items()):
            try:
                await subscriber(event)
            except Exception as e:
                logger.error(f"Error in subscriber {subscriber_id}: {e}", exc_info=True)

    async def close(self):
        await asyncio.gather(
            *[subscriber.close() for subscriber in self._subscribers.values()]
        )
        self._subscribers.clear()
