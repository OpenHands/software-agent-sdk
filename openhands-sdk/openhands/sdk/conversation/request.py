"""Conversation request models.

These types define the payload for starting and interacting with
conversations.  They live in the SDK so that ``ConversationSettings``
can reference them without a cross-package dependency on the
agent-server.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Discriminator, Field, Tag

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.agent.agent import Agent
from openhands.sdk.conversation.types import ConversationTags
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
    tags: ConversationTags = Field(
        default_factory=dict,
        description=(
            "Key-value tags for the conversation. Keys must be lowercase "
            "alphanumeric. Values are arbitrary strings up to 256 characters."
        ),
    )
    autotitle: bool = Field(
        default=True,
        description=(
            "If true, automatically generate a title for the conversation from "
            "the first user message. Uses the LLM configured via "
            "title_llm_profile if set; otherwise falls back to simple message "
            "truncation."
        ),
    )
    title_llm_profile: str | None = Field(
        default=None,
        description=(
            "Optional LLM profile name for title generation. If set, the LLM "
            "is loaded from LLMProfileStore (~/.openhands/profiles/) and used "
            "for LLM-based title generation. This enables using a fast/cheap "
            "model for titles regardless of the agent's main model. If not "
            "set, title generation falls back to simple message truncation."
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
