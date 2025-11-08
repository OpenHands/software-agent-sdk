import uuid
from collections.abc import Callable

from openhands.sdk.event.base import Event


ConversationCallbackType = Callable[[Event], None]

ConversationID = uuid.UUID
"""Type alias for conversation IDs."""
