from abc import ABC
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from openhands.agent_server.utils import OpenHandsUUID, utc_now
from openhands.sdk import LLM, AgentBase, Event, ImageContent, Message, TextContent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.utils.models import DiscriminatedUnionMixin, OpenHandsModel
from openhands.sdk.workspace import LocalWorkspace


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


class SendMessageRequest(BaseModel):
    """Payload to send a message to the agent.

    This is a simplified version of openhands.sdk.Message.
    """

    role: Literal["user", "system", "assistant", "tool"] = "user"
    content: list[TextContent | ImageContent] = Field(default_factory=list)
    run: bool = Field(
        default=False,
        description=("Whether the agent loop should automatically run if not running"),
    )

    def create_message(self) -> Message:
        message = Message(role=self.role, content=self.content)
        return message


class StartConversationRequest(BaseModel):
    """Payload to create a new conversation.

    Contains an Agent configuration along with conversation-specific options.
    """

    agent: AgentBase
    workspace: LocalWorkspace = Field(
        ...,
        description="Working directory for agent operations and tool execution",
    )
    conversation_id: OpenHandsUUID | None = Field(
        default=None,
        description=(
            "Optional conversation ID. If not provided, a random UUID will be "
            "generated."
        ),
    )
    confirmation_policy: ConfirmationPolicyBase = Field(
        default=NeverConfirm(),
        description="Controls when the conversation will prompt the user before "
        "continuing. Defaults to never.",
    )
    initial_message: SendMessageRequest | None = Field(
        default=None, description="Initial message to pass to the LLM"
    )
    max_iterations: int = Field(
        default=500,
        ge=1,
        description="If set, the max number of iterations the agent will run "
        "before stopping. This is useful to prevent infinite loops.",
    )
    stuck_detection: bool = Field(
        default=True,
        description="If true, the conversation will use stuck detection to "
        "prevent infinite loops.",
    )
    secrets: dict[str, SecretSource] = Field(
        default_factory=dict,
        description="Secrets available in the conversation",
    )
    tool_module_qualnames: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapping of tool names to their module qualnames from the client's "
            "registry. These modules will be dynamically imported on the server "
            "to register the tools for this conversation."
        ),
    )


class StoredConversation(StartConversationRequest):
    """Stored details about a conversation"""

    id: OpenHandsUUID
    title: str | None = Field(
        default=None, description="User-defined title for the conversation"
    )
    metrics: MetricsSnapshot | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationInfo(ConversationState):
    """Information about a conversation running locally without a Runtime sandbox."""

    # ConversationState already includes id and agent
    # Add additional metadata fields

    title: str | None = Field(
        default=None, description="User-defined title for the conversation"
    )
    metrics: MetricsSnapshot | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ConversationPage(BaseModel):
    items: list[ConversationInfo]
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

    title: str = Field(
        ..., min_length=1, max_length=200, description="New conversation title"
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


class BashEventSortOrder(Enum):
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMP_DESC = "TIMESTAMP_DESC"


class BashEventPage(OpenHandsModel):
    items: list[BashEventBase]
    next_page_id: str | None = None
