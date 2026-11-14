"""Hook event types and data structures."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HookEventType(str, Enum):
    """Types of hook events that can trigger hooks."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    STOP = "Stop"


class HookEvent(BaseModel):
    """Data passed to hook scripts via stdin as JSON."""

    event_type: HookEventType
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_response: dict[str, Any] | None = None
    tool_use_id: str | None = Field(
        default=None,
        description=(
            "Identifies the tool call: the ActionEvent's tool_call_id for "
            "PreToolUse and the paired ObservationEvent's tool_call_id for "
            "PostToolUse, so both hooks for one call see the same value. "
            "Null for non-tool hook events."
        ),
    )
    message: str | None = None
    session_id: str | None = None
    working_dir: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"use_enum_values": True}


class HookDecision(str, Enum):
    """Decisions a hook can make about an operation."""

    ALLOW = "allow"
    DENY = "deny"
    # ASK = "ask"  # Future: prompt user for confirmation before proceeding
