from abc import ABC
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator

from openhands.agent_server.utils import OpenHandsUUID, utc_now
from openhands.sdk import LLM, Agent, ImageContent, Message, TextContent
from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.base import Event
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.plugin import PluginSource
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
    OpenHandsModel,
    kind_of,
)
from openhands.sdk.workspace import LocalWorkspace
from openhands.sdk.workspace.base import BaseWorkspace


class AgentServerErrorEvent(Event):
    """Event emitted by the agent server when a server-level error occurs.

    This event is used for errors that originate from the agent server itself,
    such as MCP connection failures, WebSocket errors, or other infrastructure
    issues. Unlike ConversationErrorEvent which is for conversation-level failures,
    this event indicates a problem with the server environment.
    """

    code: str = Field(description="Code for the error - typically an error type")
    detail: str = Field(description="Details about the error")


ACPEnabledAgent = Annotated[
    Annotated[Agent, Tag("Agent")] | Annotated[ACPAgent, Tag("ACPAgent")],
    Discriminator(kind_of),
]


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


class _StartConversationRequestBase(BaseModel):
    """Common conversation creation fields shared by conversation contracts."""

    workspace: LocalWorkspace = Field(
        ...,
        description="Working directory for agent operations and tool execution",
    )
    conversation_id: UUID | None = Field(
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
    agent_definitions: list[AgentDefinition] = Field(
        default_factory=list,
        description=(
            "Agent definitions from the client's registry. These are "
            "registered on the server so that DelegateTool and TaskSetTool "
            "can see user-registered subagents."
        ),
    )
    plugins: list[PluginSource] | None = Field(
        default=None,
        description=(
            "List of plugins to load for this conversation. Plugins are loaded "
            "and their skills/MCP config are merged into the agent. "
            "Hooks are extracted and stored for runtime execution."
        ),
    )
    hook_config: HookConfig | None = Field(
        default=None,
        description=(
            "Optional hook configuration for this conversation. Hooks are shell "
            "scripts that run at key lifecycle events (PreToolUse, PostToolUse, "
            "UserPromptSubmit, Stop, etc.). If both hook_config and plugins are "
            "provided, they are merged with explicit hooks running before plugin "
            "hooks."
        ),
    )
    autotitle: bool = Field(
        default=True,
        description=(
            "If true, automatically generate a title for the conversation from "
            "the first user message using the conversation's LLM."
        ),
    )


class StartConversationRequest(_StartConversationRequestBase):
    """Payload to create a new conversation.

    Contains an Agent configuration along with conversation-specific options.
    """

    agent: Agent


class StartACPConversationRequest(_StartConversationRequestBase):
    """Payload to create a conversation with ACP-capable agent support."""

    agent: ACPEnabledAgent


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


class BashError(BashEventBase):
    code: str = Field(description="Code for the error - typically an error type")
    detail: str = Field(description="Details about the error")


class BashEventSortOrder(Enum):
    TIMESTAMP = "TIMESTAMP"
    TIMESTAMP_DESC = "TIMESTAMP_DESC"


class BashEventPage(OpenHandsModel):
    items: list[BashEventBase]
    next_page_id: str | None = None
