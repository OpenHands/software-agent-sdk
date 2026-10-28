from typing import Annotated, Final, Literal

from openhands.sdk.utils.masking import SkipSecretMasking


EventType = Literal["action", "observation", "message", "system_prompt", "agent_error"]
SourceType = Literal["agent", "user", "environment", "hook"]

UnmaskedID = Annotated[str, SkipSecretMasking()]
"""An identifier that secret masking never rewrites.

Use it, or ``EventID`` / ``ToolCallID``, for every id field on an event or
observation. A masked event id breaks the event file name, so the conversation
cannot reload. A masked tool-call id breaks tool-call pairing in LLM requests.
"""

EventID = UnmaskedID
"""Type alias for event IDs."""

ROOT_PARENT_ID: Final = "__root__"
"""Reserved ``parent_id`` marking a tree root created after ``navigate_to(None)``.

No event ``id`` may equal it (enforced by a validator on ``Event``), so a
``parent_id == ROOT_PARENT_ID`` unambiguously means "root", never "child of the
event whose id is ``__root__``"."""

ToolCallID = UnmaskedID
"""Type alias for tool call IDs."""
