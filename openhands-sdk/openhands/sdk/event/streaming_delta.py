import uuid
from datetime import datetime
from typing import ClassVar

from pydantic import ConfigDict, Field

from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.utils.models import DiscriminatedUnionMixin


class StreamingDeltaEvent(DiscriminatedUnionMixin):
    """Transient LLM token delta, delivered over the WebSocket only.

    Not an ``Event``: never persisted, never replayed, and published on its
    own bus. ``id``, ``timestamp`` and ``source`` are re-declared because
    browser clients require them on every frame.
    """

    # Frozen as it was under ``Event``: one instance fans out to several
    # subscribers. ``extra`` stays open so stream identity can be added later.
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    id: EventID = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    source: SourceType = "agent"
    content: str | None = None
    reasoning_content: str | None = None
