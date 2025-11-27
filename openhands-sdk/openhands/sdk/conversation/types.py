import uuid
from collections.abc import Callable

from openhands.sdk.event.base import Event
from openhands.sdk.llm.streaming import TokenCallbackType


ConversationCallbackType = Callable[[Event], None]
"""Type alias for event callback functions."""

ConversationTokenCallbackType = TokenCallbackType
"""Callback type invoked for streaming LLM deltas."""

ConversationID = uuid.UUID
"""Type alias for conversation IDs."""
