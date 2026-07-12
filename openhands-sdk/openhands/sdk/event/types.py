from typing import Final, Literal


EventType = Literal["action", "observation", "message", "system_prompt", "agent_error"]
SourceType = Literal["agent", "user", "environment", "hook"]

EventID = str
"""Type alias for event IDs."""

ROOT_PARENT_ID: Final = "__root__"
"""Reserved ``parent_id`` marking a tree root created after ``navigate_to(None)``.

No event ``id`` may equal it (enforced by a validator on ``Event``), so a
``parent_id == ROOT_PARENT_ID`` unambiguously means "root", never "child of the
event whose id is ``__root__``"."""

ToolCallID = str
"""Type alias for tool call IDs."""
