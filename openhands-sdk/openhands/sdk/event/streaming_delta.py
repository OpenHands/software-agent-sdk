from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


class StreamingDeltaEvent(Event):
    """Streaming LLM token delta for real-time WebSocket delivery."""

    source: SourceType = "agent"
    content: str | None = None
    reasoning_content: str | None = None
