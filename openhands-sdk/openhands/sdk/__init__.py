from importlib.metadata import PackageNotFoundError, version

from openhands.sdk.agent import (
    Agent,
    AgentBase,
)
from openhands.sdk.banner import _print_banner
from openhands.sdk.context import AgentContext
from openhands.sdk.context.condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.conversation import (
    BaseConversation,
    Conversation,
    ConversationCallbackType,
    ConversationExecutionStatus,
    LocalConversation,
    RemoteConversation,
)
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.event import Event, HookExecutionEvent, LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import FileStore, LocalFileStore
from openhands.sdk.llm import (
    LLM,
    FallbackStrategy,
    ImageContent,
    LLMProfileStore,
    LLMRegistry,
    LLMStreamChunk,
    Message,
    RedactedThinkingBlock,
    RegistryEvent,
    TextContent,
    ThinkingBlock,
    TokenCallbackType,
    TokenUsage,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.mcp import (
    MCPClient,
    MCPToolDefinition,
    MCPToolObservation,
    create_mcp_tools,
)
from openhands.sdk.plugin import Plugin
from openhands.sdk.settings import (
    ACPAgentSettings,
    AgentSettings,
    AgentSettingsConfig,
    CondenserSettings,
    ConversationSettings,
    LLMAgentSettings,
    SettingsChoice,
    SettingsFieldSchema,
    SettingsSchema,
    SettingsSectionSchema,
    VerificationSettings,
    default_agent_settings,
    export_agent_settings_schema,
    export_settings_schema,
    validate_agent_settings,
)
from openhands.sdk.settings.metadata import (
    SettingProminence,
    SettingsFieldMetadata,
    SettingsSectionMetadata,
    field_meta,
)
from openhands.sdk.skills import (
    load_project_skills,
    load_skills_from_dir,
    load_user_skills,
)
from openhands.sdk.subagent import (
    agent_definition_to_factory,
    load_agents_from_dir,
    load_project_agents,
    load_user_agents,
    register_agent,
)
from openhands.sdk.tool import (
    Action,
    Observation,
    Tool,
    ToolDefinition,
    list_registered_tools,
    register_tool,
    resolve_tool,
)
from openhands.sdk.utils import page_iterator
from openhands.sdk.workspace import (
    AsyncRemoteWorkspace,
    LocalWorkspace,
    RemoteWorkspace,
    Workspace,
)


try:
    __version__ = version("openhands-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for editable/unbuilt environments

# Print startup banner
_print_banner(__version__)

__all__ = [
    "LLM",
    "LLMRegistry",
    "LLMProfileStore",
    "LLMStreamChunk",
    "FallbackStrategy",
    "TokenCallbackType",
    "TokenUsage",
    "ConversationStats",
    "RegistryEvent",
    "Message",
    "TextContent",
    "ImageContent",
    "ThinkingBlock",
    "RedactedThinkingBlock",
    "Tool",
    "ToolDefinition",
    "AgentBase",
    "Agent",
    "Action",
    "Observation",
    "MCPClient",
    "MCPToolDefinition",
    "MCPToolObservation",
    "MessageEvent",
    "HookExecutionEvent",
    "create_mcp_tools",
    "get_logger",
    "Conversation",
    "BaseConversation",
    "LocalConversation",
    "RemoteConversation",
    "ConversationExecutionStatus",
    "ConversationCallbackType",
    "Event",
    "LLMConvertibleEvent",
    "AgentContext",
    "LLMSummarizingCondenser",
    "CondenserSettings",
    "ConversationSettings",
    "VerificationSettings",
    "ACPAgentSettings",
    "AgentSettings",
    "AgentSettingsConfig",
    "LLMAgentSettings",
    "default_agent_settings",
    "export_agent_settings_schema",
    "validate_agent_settings",
    "SettingsChoice",
    "SettingProminence",
    "SettingsFieldMetadata",
    "SettingsFieldSchema",
    "SettingsSchema",
    "SettingsSectionMetadata",
    "SettingsSectionSchema",
    "export_settings_schema",
    "field_meta",
    "FileStore",
    "LocalFileStore",
    "Plugin",
    "register_tool",
    "resolve_tool",
    "list_registered_tools",
    "Workspace",
    "LocalWorkspace",
    "RemoteWorkspace",
    "AsyncRemoteWorkspace",
    "register_agent",
    "load_project_agents",
    "load_user_agents",
    "load_agents_from_dir",
    "agent_definition_to_factory",
    "load_project_skills",
    "load_skills_from_dir",
    "load_user_skills",
    "page_iterator",
    "__version__",
]
