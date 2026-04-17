from openhands.sdk.event.base import Event
from openhands.sdk.event.types import SourceType


class StreamingDeltaEvent(Event):
    """Transient LLM token delta for real-time WebSocket delivery.

    Not persisted to the conversation event log: these events are published
    directly to PubSub, bypassing the callback chain that writes to
    ConversationState.events. Clients reconnecting mid-stream will receive
    the final MessageEvent from history but none of the deltas that produced
    it — deltas are a UX affordance, not part of the durable conversation
    record.
    """

    source: SourceType = "agent"
    content: str | None = None
    reasoning_content: str | None = None
