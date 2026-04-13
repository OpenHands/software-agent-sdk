from abc import ABC
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.request import (  # re-export for backward compat
    ACPEnabledAgent as ACPEnabledAgent,
    SendMessageRequest as SendMessageRequest,
    StartACPConversationRequest as StartACPConversationRequest,
    StartConversationRequest as StartConversationRequest,
)
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.types import ConversationTags
from openhands.sdk.event.base import Event
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.utils import OpenHandsUUID, utc_now
from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
    OpenHandsModel,
)
from openhands.sdk.workspace.base import BaseWorkspace


class ServerErrorEvent(Event):
    """Event emitted by the agent server when a server-level error occurs.

    This event is used for errors that originate from the agent server itself,
    such as MCP connection failures, WebSocket errors, or other infrastructure
    issues. Unlike ConversationErrorEvent which is for conversation-level failures,
    this event indicates a problem with the server environment.
    """

    code: str = Field(description="Code for the error - typically an error type")
    detail: str = Field(description="Details about the error")


class ConversationSortOrder(str, Enum):
    """Enum for conversation sorting options."""

    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    CREATED_AT_DESC = "CREATED_AT_DESC"
    UPDATED_AT_DESC = "UPDATED_AT_DESC"


class EventSortOrder(str, Enum):
    """Enum for event sorting options."""

    TIMESTAMP = "TIMESTAMP"
    TIMESTAMP_DESC = "TIMESTAMP_DESC"


class StoredConversation(StartACPConversationRequest):
    """Stored details about a conversation.

    Extends StartConversationRequest with server-assigned fields.
    """

    id: OpenHandsUUID
    title: str | None = Field(
        default=None, description="User-defined title for the conversation"
    )
    metrics: MetricsSnapshot | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class _ConversationInfoBase(BaseModel):
    """Common conversation info fields shared by conversation contracts."""

    id: UUID = Field(description="Unique conversation ID")
    workspace: BaseWorkspace = Field(
        ...,
        description=(
            "Workspace used by the agent to execute commands and read/write files. "
            "Not the process working directory."
        ),
    )
    persistence_dir: str | None = Field(
        default="workspace/conversations",
        description="Directory for persisting conversation state and events. "
        "If None, conversation will not be persisted.",
    )
    max_iterations: int = Field(
        default=500,
        gt=0,
        description=(
            "Maximum number of iterations the agent can perform in a single run."
        ),
    )
    stuck_detection: bool = Field(
        default=True,
        description="Whether to enable stuck detection for the agent.",
    )
    execution_status: ConversationExecutionStatus = Field(
        default=ConversationExecutionStatus.IDLE
    )
    confirmation_policy: ConfirmationPolicyBase = Field(default=NeverConfirm())
    security_analyzer: SecurityAnalyzerBase | None = Field(
        default=None,
        description="Optional security analyzer to evaluate action risks.",
    )
    activated_knowledge_skills: list[str] = Field(
        default_factory=list,
        description="List of activated knowledge skills name",
    )
    blocked_actions: dict[str, str] = Field(
        default_factory=dict,
        description="Actions blocked by PreToolUse hooks, keyed by action ID",
    )
    blocked_messages: dict[str, str] = Field(
        default_factory=dict,
        description="Messages blocked by UserPromptSubmit hooks, keyed by message ID",
    )
    last_user_message_id: str | None = Field(
        default=None,
        description=(
            "Most recent user MessageEvent id for hook block checks. "
            "Updated when user messages are emitted so Agent.step can pop "
            "blocked_messages without scanning the event log. If None, "
            "hook-blocked checks are skipped (legacy conversations)."
        ),
    )
    stats: ConversationStats = Field(
        default_factory=ConversationStats,
        description="Conversation statistics for tracking LLM metrics",
    )
    secret_registry: SecretRegistry = Field(
        default_factory=SecretRegistry,
        description="Registry for handling secrets and sensitive data",
    )
    agent_state: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary for agent-specific runtime state that persists across "
        "iterations.",
    )
    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Hook configuration for this conversation. Includes definitions for "
            "PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, SessionEnd, "
            "and Stop hooks."
        ),
    )

    title: str | None = Field(
        default=None, description="User-defined title for the conversation"
    )
    metrics: MetricsSnapshot | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    tags: ConversationTags = Field(
        default_factory=dict,
        description=(
            "Key-value tags for the conversation. Keys must be lowercase "
            "alphanumeric. Values are arbitrary strings up to 256 characters."
        ),
    )


class ConversationInfo(_ConversationInfoBase):
    """Information about a conversation running locally without a Runtime sandbox."""

    agent: Agent = Field(
        ...,
        description=(
            "The legacy v1 agent configuration. "
            "This endpoint remains pinned to the standard Agent contract."
        ),
    )


class ConversationPage(BaseModel):
    items: list[ConversationInfo]
    next_page_id: str | None = None


class ACPConversationInfo(_ConversationInfoBase):
    """Conversation info that supports ACP-capable agent configs."""

    agent: ACPEnabledAgent = Field(
        ...,
        description=(
            "The agent running in the conversation. "
            "Supports both Agent and ACPAgent payloads."
        ),
    )


class ACPConversationPage(BaseModel):
    items: list[ACPConversationInfo]
    next_page_id: str | None = None


class ConversationResponse(BaseModel):
    conversation_id: str
    state: ConversationExecutionStatus


class ConfirmationResponseRequest(BaseModel):
    """Payload to accept or reject a pending action."""

    accept: bool
    reason: str = "User rejected the action."


class Success(BaseModel):
    success: bool = True


class EventPage(OpenHandsModel):
    items: list[Event]
    next_page_id: str | None = None


class UpdateSecretsRequest(BaseModel):
    """Payload to update secrets in a conversation."""

    secrets: dict[str, SecretSource] = Field(
        description="Dictionary mapping secret keys to values"
    )

    @field_validator("secrets", mode="before")
    @classmethod
    def convert_string_secrets(cls, v: dict[str, Any]) -> dict[str, Any]:
        """Convert plain string secrets to StaticSecret objects.

        This validator enables backward compatibility by automatically converting:
        - Plain strings: "secret-value" → StaticSecret(value=SecretStr("secret-value"))
        - Dict with value field: {"value": "secret-value"} → StaticSecret dict format
        - Proper SecretSource objects: passed through unchanged
        """
        if not isinstance(v, dict):
            return v

        converted = {}
        for key, value in v.items():
            if isinstance(value, str):
                # Convert plain string to StaticSecret dict format
                converted[key] = {
                    "kind": "StaticSecret",
                    "value": value,
                }
            elif isinstance(value, dict):
                if "value" in value and "kind" not in value:
                    # Convert dict with value field to StaticSecret dict format
                    converted[key] = {
                        "kind": "StaticSecret",
                        "value": value["value"],
                    }
                else:
                    # Keep existing SecretSource objects or properly formatted dicts
                    converted[key] = value
            else:
                # Keep other types as-is (will likely fail validation later)
                converted[key] = value

        return converted


class SetConfirmationPolicyRequest(BaseModel):
    """Payload to set confirmation policy for a conversation."""

    policy: ConfirmationPolicyBase = Field(description="The confirmation policy to set")


class SetSecurityAnalyzerRequest(BaseModel):
    "Payload to set security analyzer for a conversation"

    security_analyzer: SecurityAnalyzerBase | None = Field(
        description="The security analyzer to set"
    )


class UpdateConversationRequest(BaseModel):
    """Payload to update conversation metadata."""

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="New conversation title",
    )
    tags: ConversationTags | None = Field(
        default=None,
        description=(
            "Key-value tags to set on the conversation. Keys must be lowercase "
            "alphanumeric. Values are arbitrary strings up to 256 characters. "
            "Replaces all existing tags when provided."
        ),
    )


class GenerateTitleRequest(BaseModel):
    """Payload to generate a title for a conversation."""

    max_length: int = Field(
        default=50, ge=1, le=200, description="Maximum length of the generated title"
    )
    llm: LLM | None = Field(
        default=None, description="Optional LLM to use for title generation"
    )


class GenerateTitleResponse(BaseModel):
    """Response containing the generated conversation title."""

    title: str = Field(description="The generated title for the conversation")


class AskAgentRequest(BaseModel):
    """Payload to ask the agent a simple question."""

    question: str = Field(description="The question to ask the agent")


class AskAgentResponse(BaseModel):
    """Response containing the agent's answer."""

    response: str = Field(description="The agent's response to the question")


class AgentResponseResult(BaseModel):
    """The agent's final response for a conversation.

    Contains the text of the last agent finish message or text response.
    Empty string if the agent has not produced a final response yet.
    """

    response: str = Field(
        description=(
            "The agent's final response text. Extracted from either a "
            "FinishAction message or the last agent MessageEvent. "
            "Empty string if no final response is available."
        )
    )


class BashEventBase(DiscriminatedUnionMixin, ABC):
    """Base class for all bash event types"""

    id: OpenHandsUUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=utc_now)


class ExecuteBashRequest(BaseModel):
    command: str = Field(description="The bash command to execute")
    cwd: str | None = Field(default=None, description="The current working directory")
    timeout: int = Field(
        default=300,
        description="The max number of seconds a command may be permitted to run.",
    )


class BashCommand(BashEventBase, ExecuteBashRequest):
    pass


class BashOutput(BashEventBase):
    """
    Output of a bash command. A single command may have multiple pieces of output
    depending on how large the output is.
    """

    command_id: OpenHandsUUID
    order: int = Field(
        default=0, description="The order for this output, sequentially starting with 0"
    )
    exit_code: int | None = Field(
        default=None, description="Exit code None implies the command is still running."
    )
    stdout: str | None = Field(
        default=None, description="The standard output from the command"
    )
    stderr: str | None = Field(
        default=None, description="The error output from the command"
    )


class BashError(BashEventBase):
    code: str = Field(description="Code for the error - typically an error type")
    detail: str = Field(description="Details about the error")


class BashEventSortOrder(Enum):
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMP_DESC = "TIMESTAMP_DESC"


class BashEventPage(OpenHandsModel):
    items: list[BashEventBase]
    next_page_id: str | None = None
