"""Conversation request models.

These types define the payload for starting and interacting with
conversations.  They live in the SDK so that ``ConversationSettings``
can reference them without a cross-package dependency on the
agent-server.
"""

from __future__ import annotations

from typing import Annotated, Any, cast
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
from openhands.sdk.conversation.message_request import (
    SendMessageRequest as SendMessageRequest,
)
from openhands.sdk.conversation.types import (
    ConversationObservabilityMetadata,
    ConversationObservabilitySpanName,
    ConversationObservabilityTags,
    ConversationTags,
)
from openhands.sdk.hooks import HookConfig
from openhands.sdk.plugin import PluginSource
from openhands.sdk.profiles.agent_profile import (
    AgentProfile,
    validate_agent_profile,
)
from openhands.sdk.profiles.resolver import (
    AgentLaunchAdditions as AgentLaunchAdditions,
)
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


class ConversationConfig(BaseModel):
    """Shared conversation configuration — everything except the agent.

    This is the common base for :class:`StartConversationRequest` (which adds the
    ``agent``/``agent_settings``/``agent_profile_id`` family) and the
    agent-server's ``StoredConversation`` (which does NOT persist the agent —
    the agent's single source of truth is ``ConversationState`` /
    ``base_state.json``). Keeping the agent off this base is what makes the
    duplication structurally impossible rather than something a reviewer has to
    remember to exclude.
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
    parent_conversation_id: UUID | None = Field(
        default=None,
        description=(
            "Optional ID of an existing conversation that owns this one. The "
            "parent must already exist and share this conversation's workspace."
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
    agent_launch_additions: AgentLaunchAdditions | None = Field(
        default=None,
        description=(
            "Deployment context applied after agent or Agent Profile "
            "resolution. The stored Agent Profile is not modified."
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
            "Optional user ID supplied by the hosting deployment, used to "
            "correlate this conversation with the identity that deployment "
            "already established. When set it is passed to "
            "Laminar.set_trace_user_id() so traces can be queried by user, "
            "and — where a host has enabled product analytics — it is reused "
            "verbatim as the analytics correlation id so events attach to the "
            "existing person rather than creating a duplicate identity. It is "
            "never generated by the SDK, and is omitted entirely when unset."
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
    observability_span_name: ConversationObservabilitySpanName = Field(
        default="conversation",
        description=(
            "Optional named child span to emit under the conversation root. Use "
            "stable, low-cardinality names because observability backends may use "
            "span names for grouping or signal routing."
        ),
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


class StartConversationRequest(ConversationConfig):
    """Payload to create a new conversation.

    Extends :class:`ConversationConfig` with the agent source: a stored Agent
    Profile (``agent_profile_id``), an inline one (``agent_profile``), a
    concrete ``agent`` built in code, or the deprecated ``agent_settings``.
    Profiles and ``agent_settings`` are resolved by the server, not here.

    Note: the agent lives here on the *request*, deliberately not on
    ``ConversationConfig``. The persisted record (``StoredConversation``) does
    not carry the agent — its single source of truth is ``ConversationState`` /
    ``base_state.json``.
    """

    agent_settings: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Deprecated since v1.50.0 and scheduled for removal in v1.55.0. "
            "Use `agent_profile_id` or `agent_profile`. An agent settings "
            "payload, validated with the `agent_kind` discriminator. The server "
            "launches it as an inline Agent Profile. Ignored when `agent` is set."
        ),
        json_schema_extra={"deprecated": True},
    )
    agent_profile_id: UUID | None = Field(
        default=None,
        description=(
            "Stored Agent Profile to launch. The server resolves it. Mutually "
            "exclusive with the other agent sources."
        ),
    )
    agent_profile: AgentProfile | None = Field(
        default=None,
        description=(
            "Inline Agent Profile draft to launch. Resolved exactly like a "
            "stored profile and not saved. Mutually exclusive with the other "
            "agent sources."
        ),
    )
    agent: AgentBase = Field(default=cast(AgentBase, None))

    @model_validator(mode="before")
    @classmethod
    def _normalize_agent_source(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # An explicit null means "not this source": clients round-trip the whole
        # family, and ``agent`` has no null-able type of its own.
        payload = {
            name: value
            for name, value in data.items()
            if value is not None
            or name not in ("agent", "agent_settings", "agent_profile")
        }
        profile_sources = [
            name
            for name in ("agent_profile_id", "agent_profile")
            if payload.get(name) is not None
        ]
        other_sources = [
            name
            for name in ("agent", "agent_settings")
            if payload.get(name) is not None
        ]
        if len(profile_sources) + bool(other_sources) > 1:
            raise ValueError(
                f"`{profile_sources[0]}` is mutually exclusive with "
                + ", ".join(
                    f"`{name}`" for name in [*profile_sources[1:], *other_sources]
                )
            )
        if payload.get("agent_profile") is not None:
            payload["agent_profile"] = validate_agent_profile(payload["agent_profile"])
        if isinstance(payload.get("agent"), dict):
            agent_payload = dict(payload["agent"])
            if "kind" not in agent_payload and "llm" in agent_payload:
                agent_payload["kind"] = "Agent"
            payload["agent"] = agent_payload
        return payload

    @model_validator(mode="after")
    def _require_agent(self) -> StartConversationRequest:
        has_profile = (
            self.agent_profile_id is not None or self.agent_profile is not None
        )
        if not has_profile and self.agent is None and self.agent_settings is None:
            raise ValueError(
                "One of `agent`, `agent_profile_id`, `agent_profile`, or"
                " `agent_settings` must be provided"
            )
        additions = self.agent_launch_additions
        if additions is not None and additions.llm_profile_ref and not has_profile:
            raise ValueError(
                "`agent_launch_additions.llm_profile_ref` requires"
                " `agent_profile_id` or `agent_profile`"
            )
        return self

    @field_serializer("agent", mode="wrap")
    def _serialize_agent(self, value: AgentBase | None, handler: Any) -> Any:
        if value is None:
            return None
        return handler(value)
