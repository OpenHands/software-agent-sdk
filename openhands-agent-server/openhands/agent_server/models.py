from __future__ import annotations

from abc import ABC
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from openhands.sdk import LLM
from openhands.sdk.agent.acp_models import ACPModelInfo
from openhands.sdk.agent.base import AgentBase
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
from openhands.sdk.llm.message import (  # re-export
    ImageContent as ImageContent,
    TextContent as TextContent,
)
from openhands.sdk.llm.utils.metrics import MetricsSnapshot
from openhands.sdk.profiles.agent_profile import (
    LaunchedAgentProfile as LaunchedAgentProfile,
)
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.tool.client_tool import ClientToolSpec
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


class ConversationSortOrder(StrEnum):
    """Enum for conversation sorting options."""

    CREATED_AT = "CREATED_AT"
    UPDATED_AT = "UPDATED_AT"
    CREATED_AT_DESC = "CREATED_AT_DESC"
    UPDATED_AT_DESC = "UPDATED_AT_DESC"


class EventSortOrder(StrEnum):
    """Enum for event sorting options."""

    TIMESTAMP = "TIMESTAMP"
    TIMESTAMP_DESC = "TIMESTAMP_DESC"


class StoredConversation(StartConversationRequest):
    """Stored details about a conversation.

    Extends StartConversationRequest with server-assigned fields.
    """

    # agent_profile_id is resolved into launched_agent_profile at creation; exclude from
    # the persistence payload so it does not re-appear in meta.json.
    agent_profile_id: UUID | None = Field(default=None, exclude=True)

    id: OpenHandsUUID
    title: str | None = Field(
        default=None, description="User-defined title for the conversation"
    )
    metrics: MetricsSnapshot | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    parent_conversation_id: OpenHandsUUID | None = Field(
        default=None,
        description=(
            "ID of the conversation this one was forked from. ``None`` for "
            "conversations created directly (not via fork)."
        ),
    )
    forked_from_event_id: str | None = Field(
        default=None,
        description=(
            "Event ID this conversation was forked at (the source's HEAD at "
            "fork time, or the explicit ``from_event_id`` branch point). "
            "``None`` for non-forked conversations or whole-conversation forks."
        ),
    )
    launched_agent_profile: LaunchedAgentProfile | None = Field(
        default=None,
        description=(
            "Provenance snapshot of the agent profile that launched this "
            "conversation. Set at creation when `agent_profile_id` is supplied; "
            "``None`` for conversations started directly with `agent` or "
            "`agent_settings`."
        ),
    )


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
    invoked_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Names of progressive-disclosure skills explicitly invoked via the "
            "`invoke_skill` tool."
        ),
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
    leaf_event_id: str | None = Field(
        default=None,
        description=(
            "HEAD of the conversation tree: the parent of the next appended "
            "event. ``None`` means an empty tree (or, for pre-feature "
            "conversations, the linear tail). Moving it via ``navigate`` "
            "re-roots the active branch the agent runs on."
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
    # Plain ``UUID`` (not ``OpenHandsUUID``) so it JSON-serializes dashed, exactly
    # like the ``id`` field above — clients must be able to correlate the two.
    parent_conversation_id: UUID | None = Field(
        default=None,
        description=(
            "ID of the conversation this one was forked from. ``None`` for "
            "conversations created directly (not via fork)."
        ),
    )
    forked_from_event_id: str | None = Field(
        default=None,
        description=(
            "Event ID this conversation was forked at. ``None`` for non-forked "
            "conversations or whole-conversation forks."
        ),
    )

    tags: ConversationTags = Field(
        default_factory=dict,
        description=(
            "Key-value tags for the conversation. Keys must be lowercase "
            "alphanumeric. Values are arbitrary strings up to 256 characters."
        ),
    )
    current_model_id: str | None = Field(
        default=None,
        description=(
            "Model the agent is actually using for this session. For ACP "
            "agents, this is lifted off ``ACPAgent.current_model_id`` "
            "(populated from the ``models.currentModelId`` field on the "
            "ACP session response, or from ``acp_model`` when the caller "
            "forced an override). May be an opaque alias (e.g. "
            'claude-agent-acp\'s ``"default"``); match it against '
            "``available_models`` to get a display label. ``None`` for older "
            "ACP servers that don't surface the field, or while the agent is "
            "still initializing. Native OpenHands agents leave this ``None`` — "
            "consumers should read ``agent.llm.model`` for those."
        ),
    )
    available_models: list[ACPModelInfo] = Field(
        default_factory=list,
        description=(
            "Models the ACP server offers for this session, lifted off "
            "``ACPAgent.available_models`` (the ``models.availableModels`` "
            "field on the ACP session response). Each entry carries a "
            "``model_id`` plus an optional ``name``/``description``. Surfaced "
            "verbatim so clients can render a model picker and resolve "
            "``current_model_id`` to a display label themselves — the server "
            "does no name curation. Empty for ACP servers that don't surface "
            "the (UNSTABLE) capability and for native OpenHands agents. "
            "Client contract: ``current_model_id`` is NOT guaranteed to be a "
            "member — a forced ``acp_model`` override may name a model absent "
            "from the list — so treat a miss as 'show the raw id'. Some "
            "entries are opaque aliases whose human identity lives in "
            '``description`` (e.g. claude-agent-acp\'s ``"default"`` -> '
            '``"Opus 4.7 with 1M context · ..."``).'
        ),
    )
    supports_runtime_model_switch: bool = Field(
        default=False,
        description=(
            "Whether a live, mid-conversation model switch will be attempted "
            "for this conversation — "
            "tells the inline picker whether to offer a live-switch control. "
            "Mirrors the SDK's switch gate: ``True`` for known switch-capable "
            "providers; ``False`` for unknown/custom ACP servers because their "
            "generic config writes are not guaranteed live-switch primitives. "
            "``False`` for native "
            "OpenHands agents, for a known provider that declares no support, "
            "and before the conversation has started a session."
        ),
    )
    launched_agent_profile: LaunchedAgentProfile | None = Field(
        default=None,
        description=(
            "Provenance snapshot of the agent profile that launched this "
            "conversation. Set at creation when the conversation was started via "
            "``agent_profile_id``; ``None`` for conversations started directly "
            "with ``agent`` or ``agent_settings``. Clients use this to identify "
            "which agent profile is current without fragile settings-comparison."
        ),
    )


class ConversationInfo(_ConversationInfoBase):
    """Information about a conversation running locally without a Runtime sandbox."""

    agent: AgentBase = Field(
        ...,
        description="The agent running in the conversation.",
    )
    client_tools: list[ClientToolSpec] = Field(
        default_factory=list,
        description=(
            "Client-defined tool specs registered for this conversation. "
            "Surfaced so that a client re-attaching by conversation id can "
            "register the dynamic ClientAction_* action types before syncing "
            "persisted events, avoiding 'Unknown kind' deserialization errors."
        ),
    )


class ConversationPage(BaseModel):
    items: list[ConversationInfo]
    next_page_id: str | None = None


INCLUDE_SKILLS_PARAM_TITLE = (
    "Whether to include ``agent.agent_context.skills`` in the response. "
    "Default ``false`` (breaking change as of this release): skills are "
    "trimmed to ``[]`` on the wire because no known consumer reads them "
    "from HTTP responses, and a stock agent inlines ~260 KB of skill "
    "content per fetch. Pass ``true`` to opt back into the legacy "
    "full-payload shape — useful only for callers that still rely on "
    "``RemoteConversation.agent.agent_context.skills`` round-tripping "
    "over the wire. The persisted conversation state on disk and the "
    "in-memory runtime copy are untouched either way."
)


def trim_conversation_response_skills(info: ConversationInfo) -> ConversationInfo:
    """Return ``info`` with ``agent.agent_context.skills`` set to ``[]``.

    Applied **by default** on every route that emits ``ConversationInfo``
    (search, get, batch-get, start, fork, and the deprecated ACP
    equivalents). Callers that still need the legacy shape can opt in
    with ``?include_skills=true``.

    The trim exists because when an ``AgentContext`` is constructed
    with ``load_user_skills=True`` / ``load_public_skills=True``, its
    model_validator resolves the entire skill catalog (~40 entries in
    stock setups) and persists them inline. Every conversation fetch
    therefore carried ~260 KB of skill content that no known client
    actually reads from the HTTP response (agent-canvas, OpenHands
    app-server, SDK examples all ignore the field on
    ``ConversationInfo`` — they either use the in-process
    ``LocalConversation`` directly or read other fields like
    ``agent.llm.model``).

    The persisted ``ConversationState`` on disk and the in-memory copy
    held by the agent's runtime are untouched.

    A ``model_copy`` chain is enough because ``BaseModel.model_copy``
    is shallow on default — we replace the leaf ``skills`` list with
    an empty list without touching any other field. The returned
    object is a fresh ``ConversationInfo`` instance; callers that
    hold the input reference observe no mutation.
    """
    agent_ctx = getattr(info.agent, "agent_context", None)
    if agent_ctx is None or not agent_ctx.skills:
        return info
    trimmed_agent_context = agent_ctx.model_copy(update={"skills": []})
    trimmed_agent = info.agent.model_copy(
        update={"agent_context": trimmed_agent_context}
    )
    return info.model_copy(update={"agent": trimmed_agent})


# Deprecated compatibility aliases for the old ACP-specific response names.
# Keep runtime assignment aliases so existing imports still resolve to the
# canonical Pydantic models; PEP 695 ``type`` aliases would not preserve that.
ACPConversationInfo: TypeAlias = ConversationInfo  # noqa: UP040
ACPConversationPage: TypeAlias = ConversationPage  # noqa: UP040


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


class ForkConversationRequest(BaseModel):
    """Payload to fork a conversation."""

    id: UUID | None = Field(
        default=None,
        description="ID for the forked conversation (auto-generated if null)",
    )
    title: str | None = Field(
        default=None,
        max_length=200,
        description="Optional title for the forked conversation",
    )
    tags: ConversationTags | None = Field(
        default=None,
        description=(
            "Optional tags for the forked conversation. Keys must be "
            "lowercase alphanumeric."
        ),
    )
    reset_metrics: bool = Field(
        default=True,
        description=(
            "If true, cost/token stats start fresh on the fork. "
            "If false, metrics are copied from the source."
        ),
    )
    from_event_id: str | None = Field(
        default=None,
        description=(
            "If set, fork only the branch up to and including this event and "
            "set the fork's HEAD there. If null (default), copy the whole "
            "conversation."
        ),
    )


class NavigateConversationRequest(BaseModel):
    """Payload to move a conversation's HEAD to an existing event."""

    event_id: str | None = Field(
        default=None,
        description=(
            "Event to make the new HEAD, re-rooting the active branch the agent "
            "runs on. All branches stay on disk; this creates no new "
            "conversation. ``None`` selects the empty tree."
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


class StartGoalRequest(BaseModel):
    """Payload to start a ``/goal`` loop inside a conversation."""

    objective: str = Field(description="The goal objective to pursue and audit.")
    max_iterations: int = Field(
        default=10, ge=1, description="Maximum audit rounds before giving up."
    )


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
