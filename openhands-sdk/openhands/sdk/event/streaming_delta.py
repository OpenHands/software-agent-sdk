import uuid
from datetime import datetime
from typing import ClassVar

from pydantic import ConfigDict, Field

from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.utils.models import DiscriminatedUnionMixin


class StreamingDeltaEvent(DiscriminatedUnionMixin):
    """Transient LLM token delta for real-time WebSocket delivery.

    Deliberately **not** an ``Event``. Deltas are never persisted, never
    replayed and never converted to an LLM message, so they ride their own
    pub/sub bus and their own subscriber list — the same shape bash output
    events already use. Being an ``Event`` opted them into every consumer of
    the durable bus (webhooks, telemetry) by default; being a plain model
    makes that structurally impossible.

    The wire format is unchanged: ``kind`` is the class name, and ``id``,
    ``timestamp`` and ``source`` are re-declared here because browser clients
    require them on every frame.
    """

    # Frozen, as it was under ``Event``: one delta instance fans out to several
    # subscribers concurrently. ``extra`` is deliberately left at the default,
    # so stream identity can be added later without breaking older clients.
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: EventID = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique delta id (UUID)",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="Delta timestamp",
    )
    source: SourceType = "agent"
    content: str | None = None
    reasoning_content: str | None = None
