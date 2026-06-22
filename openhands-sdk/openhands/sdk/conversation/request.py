"""Conversation request models.

These types define the payload for starting and interacting with
conversations.  They live in the SDK so that ``ConversationSettings``
can reference them without a cross-package dependency on the
agent-server.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    Discriminator,
    Field,
    Tag,
    field_serializer,
    model_validator,
)

from openhands.sdk.agent.acp_agent import ACPAgent as ACPAgent
from openhands.sdk.agent.agent import Agent as Agent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.types import (
    ConversationObservabilityMetadata,
    ConversationObservabilityTags,
    ConversationTags,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.llm.message import ImageContent, Message, TextContent
from openhands.sdk.plugin import PluginSource
from openhands.sdk.secret import SecretSource
from openhands.sdk.security.analyzer import SecurityAnalyzerBase
from openhands.sdk.security.confirmation_policy import (
    ConfirmationPolicyBase,
    NeverConfirm,
)
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.tool.client_tool import ClientToolSpec
from openhands.sdk.utils.models import kind_of
from openhands.sdk.workspace import LocalWorkspace


# ---------------------------------------------------------------------------
# Helper type alias
# ---------------------------------------------------------------------------

ACPEnabledAgent = Annotated[
    Annotated[Agent, Tag("Agent")] | Annotated[ACPAgent, Tag("ACPAgent")],
    Discriminator(kind_of),
]
"""Discriminated union: either a regular Agent or an ACP-capable Agent."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SendMessageRequest(BaseModel):
    """Payload to send a message to the agent."""

    role: Literal["user", "system", "assistant", "tool"] = "user"
    content: list[TextContent | ImageContent] = Field(default_factory=list)
    run: bool = Field(
        default=False,
        description="Whether the agent loop should automatically run if not running",
    )

    def create_message(self) -> Message:
        return Message(role=self.role, content=self.content)


class StartConversationRequest(BaseModel):
    """Payload to create a new conversation.

    Supports any concrete :class:`AgentBase` implementation, including regular
    OpenHands agents and ACP agents. Clients may provide either a concrete
    ``agent`` payload or an ``agent_settings`` payload; when ``agent_settings``
    is provided without ``agent``, the settings are validated with the
    ``agent_kind`` discriminator and converted to the appropriate agent type.
    """

    workspace: LocalWorkspace = Field(
        ...,
        description="Working directory for agent operations and tool execution.",
    )
    worktree: bool = Field(
        default=False,
        description=(
            "If true and the workspace is already inside a git repository, create "
            "a dedicated git worktree for this conversation under "
            "`/tmp/conversation-worktrees/<conversation_id>/<project_name>`."
        ),
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
    security_analyzer: SecurityAnalyzerBase | None = Field(
        default=None,
        description="Optional security analyzer to evaluate action risks.",
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
    secrets_encrypted: bool = Field(
        default=False,
        description=(
            "If true, indicates that secret values in the agent configuration "
            "are cipher-encrypted and should be decrypted by the server before "
            "use. This enables secure round-tripping of settings through "
            "untrusted clients (e.g., frontend) that received encrypted values "
            "via the X-Expose-Secrets header. "
            "Flow: client calls GET /api/settings with X-Expose-Secrets: encrypted "
            "to receive cipher-encrypted secrets, then passes them in the agent "
            "config with secrets_encrypted=True so the server can decrypt them."
        ),
    )
    tool_module_qualnames: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapping of tool names to their module qualnames from the client's "
            "registry. These modules will be dynamically imported on the server "
            "to register the tools for this conversation."
        ),
    )
    client_tools: list[ClientToolSpec] = Field(
        default_factory=list,
        description=(
            "Tools defined by the client via JSON spec. These tools have "
            "no server-side executor — when the agent calls them, an "
            "ActionEvent is emitted over the WebSocket and the client "
            "handles execution. The SDK returns an acknowledgment "
            "observation immediately."
        ),
    )
    agent_definitions: list[AgentDefinition] = Field(
        default_factory=list,
        description=(
            "Agent definitions from the client's registry. These are "
            "registered on the server so that task tools "
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
    tags: ConversationTags = Field(
        default_factory=dict,
        description=(
            "Key-value tags for the conversation. Keys must be lowercase "
            "alphanumeric. Values are arbitrary strings up to 256 characters."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "Optional user ID to associate with observability traces. "
            "When set, this is passed to Laminar.set_trace_user_id() so "
            "traces can be queried by user."
        ),
    )
    observability_metadata: ConversationObservabilityMetadata = Field(
        default_factory=dict,
        description=(
            "Trace-level metadata to attach to observability backends. Values must "
            "be scalars or homogeneous scalar lists supported by OpenTelemetry."
        ),
    )
    observability_tags: ConversationObservabilityTags = Field(
        default_factory=list,
        description="Tags to attach to the conversation root observability span.",
    )
    autotitle: bool = Field(
        default=True,
        description=(
            "If true, automatically generate a title for the conversation from "
            "the first user message. Precedence: title_llm_profile (if set and "
            "loads) → agent.llm → message truncation."
        ),
    )
    title_llm_profile: str | None = Field(
        default=None,
        description=(
            "Optional LLM profile name for title generation. If set, the LLM "
            "is loaded from LLMProfileStore (~/.openhands/profiles/) and used "
            "for LLM-based title generation. This enables using a fast/cheap "
            "model for titles regardless of the agent's main model. If not "
            "set (or profile loading fails), title generation falls back to "
            "the agent's LLM."
        ),
    )

    agent_settings: dict[str, Any] | None = Field(
        default=None,
        exclude=True,
        description=(
            "Optional agent settings payload. If `agent` is omitted, this is "
            "validated with the AgentSettingsBase `agent_kind` discriminator and "
            "used to construct the concrete agent."
        ),
    )
    agent_profile_id: UUID | None = Field(
        default=None,
        description=(
            "Optional agent profile ID. When set, the agent-server resolves the "
            "referenced profile server-side (stores + cipher are required) and "
            "builds the agent from it. Mutually exclusive with `agent` and "
            "`agent_settings`. The SDK validator enforces exclusivity only — "
            "resolution happens in conversation_service, not here."
        ),
    )
    agent: AgentBase = Field(default=cast(AgentBase, None))

    @model_validator(mode="before")
    @classmethod
    def _populate_agent_from_settings(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        has_profile_id = payload.get("agent_profile_id") is not None
        has_agent = payload.get("agent") is not None
        has_agent_settings = payload.get("agent_settings") is not None
        if has_profile_id and (has_agent or has_agent_settings):
            raise ValueError(
                "`agent_profile_id` is mutually exclusive with"
                " `agent` and `agent_settings`"
            )
        if not has_profile_id:
            if payload.get("agent") is None and has_agent_settings:
                from openhands.sdk.settings.model import validate_agent_settings

                try:
                    payload["agent"] = validate_agent_settings(
                        payload["agent_settings"]
                    ).create_agent()
                except (TypeError, ValueError) as exc:
                    raise ValueError(str(exc)) from exc
            elif isinstance(payload.get("agent"), dict):
                agent_payload = dict(payload["agent"])
                if "kind" not in agent_payload and "llm" in agent_payload:
                    agent_payload["kind"] = "Agent"
                payload["agent"] = agent_payload
        return payload

    @model_validator(mode="after")
    def _require_agent(self) -> StartConversationRequest:
        if self.agent is None and self.agent_profile_id is None:
            raise ValueError(
                "One of `agent`, `agent_settings`, or"
                " `agent_profile_id` must be provided"
            )
        return self

    @field_serializer("agent", mode="wrap")
    def _serialize_agent(self, value: AgentBase | None, handler: Any) -> Any:
        if value is None:
            return None
        return handler(value)


class StartACPConversationRequest(StartConversationRequest):
    """Deprecated compatibility alias for ACP-capable start requests.

    Use :class:`StartConversationRequest` instead. It now supports both regular
    OpenHands agents and ACP agents through the same request contract.
    """
