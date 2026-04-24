from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from openhands.sdk._lazy_imports import import_lazy_symbol, lazy_dir
from openhands.sdk.banner import _print_banner


if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.context.agent_context import AgentContext
    from openhands.sdk.context.condenser.llm_summarizing_condenser import (
        LLMSummarizingCondenser,
    )
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.conversation import Conversation
    from openhands.sdk.conversation.conversation_stats import ConversationStats
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.impl.remote_conversation import RemoteConversation
    from openhands.sdk.conversation.state import ConversationExecutionStatus
    from openhands.sdk.conversation.types import ConversationCallbackType
    from openhands.sdk.event.base import Event, LLMConvertibleEvent
    from openhands.sdk.event.hook_execution import HookExecutionEvent
    from openhands.sdk.event.llm_convertible import MessageEvent
    from openhands.sdk.io.base import FileStore
    from openhands.sdk.io.local import LocalFileStore
    from openhands.sdk.llm.fallback_strategy import FallbackStrategy
    from openhands.sdk.llm.llm import LLM
    from openhands.sdk.llm.llm_profile_store import LLMProfileStore
    from openhands.sdk.llm.llm_registry import LLMRegistry, RegistryEvent
    from openhands.sdk.llm.message import (
        ImageContent,
        Message,
        RedactedThinkingBlock,
        TextContent,
        ThinkingBlock,
    )
    from openhands.sdk.llm.streaming import LLMStreamChunk, TokenCallbackType
    from openhands.sdk.llm.utils.metrics import TokenUsage
    from openhands.sdk.logger.logger import get_logger
    from openhands.sdk.mcp.client import MCPClient
    from openhands.sdk.mcp.definition import MCPToolObservation
    from openhands.sdk.mcp.tool import MCPToolDefinition
    from openhands.sdk.mcp.utils import create_mcp_tools
    from openhands.sdk.plugin.plugin import Plugin
    from openhands.sdk.settings.metadata import (
        SettingProminence,
        SettingsFieldMetadata,
        SettingsSectionMetadata,
        field_meta,
    )
    from openhands.sdk.settings.model import (
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
    from openhands.sdk.skills.skill import (
        load_project_skills,
        load_skills_from_dir,
        load_user_skills,
    )
    from openhands.sdk.subagent.load import (
        load_agents_from_dir,
        load_project_agents,
        load_user_agents,
    )
    from openhands.sdk.subagent.registry import (
        agent_definition_to_factory,
        register_agent,
    )
    from openhands.sdk.tool.registry import (
        list_registered_tools,
        register_tool,
        resolve_tool,
    )
    from openhands.sdk.tool.schema import Action, Observation
    from openhands.sdk.tool.spec import Tool
    from openhands.sdk.tool.tool import ToolDefinition
    from openhands.sdk.utils.paging import page_iterator
    from openhands.sdk.workspace.local import LocalWorkspace
    from openhands.sdk.workspace.remote import AsyncRemoteWorkspace, RemoteWorkspace
    from openhands.sdk.workspace.workspace import Workspace


try:
    __version__ = version("openhands-sdk")
except PackageNotFoundError:
    __version__ = "0.0.0"  # fallback for editable/unbuilt environments

# Print the startup banner before importing the rest of the SDK surface.
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

_LAZY_IMPORTS = {
    "LLM": (".llm.llm", "LLM"),
    "LLMRegistry": (".llm.llm_registry", "LLMRegistry"),
    "LLMProfileStore": (".llm.llm_profile_store", "LLMProfileStore"),
    "LLMStreamChunk": (".llm.streaming", "LLMStreamChunk"),
    "FallbackStrategy": (".llm.fallback_strategy", "FallbackStrategy"),
    "TokenCallbackType": (".llm.streaming", "TokenCallbackType"),
    "TokenUsage": (".llm.utils.metrics", "TokenUsage"),
    "ConversationStats": (".conversation.conversation_stats", "ConversationStats"),
    "RegistryEvent": (".llm.llm_registry", "RegistryEvent"),
    "Message": (".llm.message", "Message"),
    "TextContent": (".llm.message", "TextContent"),
    "ImageContent": (".llm.message", "ImageContent"),
    "ThinkingBlock": (".llm.message", "ThinkingBlock"),
    "RedactedThinkingBlock": (".llm.message", "RedactedThinkingBlock"),
    "Tool": (".tool.spec", "Tool"),
    "ToolDefinition": (".tool.tool", "ToolDefinition"),
    "AgentBase": (".agent.base", "AgentBase"),
    "Agent": (".agent.agent", "Agent"),
    "Action": (".tool.schema", "Action"),
    "Observation": (".tool.schema", "Observation"),
    "MCPClient": (".mcp.client", "MCPClient"),
    "MCPToolDefinition": (".mcp.tool", "MCPToolDefinition"),
    "MCPToolObservation": (".mcp.definition", "MCPToolObservation"),
    "MessageEvent": (".event.llm_convertible", "MessageEvent"),
    "HookExecutionEvent": (".event.hook_execution", "HookExecutionEvent"),
    "create_mcp_tools": (".mcp.utils", "create_mcp_tools"),
    "get_logger": (".logger.logger", "get_logger"),
    "Conversation": (".conversation.conversation", "Conversation"),
    "BaseConversation": (".conversation.base", "BaseConversation"),
    "LocalConversation": (".conversation.impl.local_conversation", "LocalConversation"),
    "RemoteConversation": (
        ".conversation.impl.remote_conversation",
        "RemoteConversation",
    ),
    "ConversationExecutionStatus": (
        ".conversation.state",
        "ConversationExecutionStatus",
    ),
    "ConversationCallbackType": (".conversation.types", "ConversationCallbackType"),
    "Event": (".event.base", "Event"),
    "LLMConvertibleEvent": (".event.base", "LLMConvertibleEvent"),
    "AgentContext": (".context.agent_context", "AgentContext"),
    "LLMSummarizingCondenser": (
        ".context.condenser.llm_summarizing_condenser",
        "LLMSummarizingCondenser",
    ),
    "CondenserSettings": (".settings.model", "CondenserSettings"),
    "ConversationSettings": (".settings.model", "ConversationSettings"),
    "VerificationSettings": (".settings.model", "VerificationSettings"),
    "ACPAgentSettings": (".settings.model", "ACPAgentSettings"),
    "AgentSettings": (".settings.model", "AgentSettings"),
    "AgentSettingsConfig": (".settings.model", "AgentSettingsConfig"),
    "LLMAgentSettings": (".settings.model", "LLMAgentSettings"),
    "default_agent_settings": (".settings.model", "default_agent_settings"),
    "export_agent_settings_schema": (
        ".settings.model",
        "export_agent_settings_schema",
    ),
    "validate_agent_settings": (".settings.model", "validate_agent_settings"),
    "SettingsChoice": (".settings.model", "SettingsChoice"),
    "SettingProminence": (".settings.metadata", "SettingProminence"),
    "SettingsFieldMetadata": (".settings.metadata", "SettingsFieldMetadata"),
    "SettingsFieldSchema": (".settings.model", "SettingsFieldSchema"),
    "SettingsSchema": (".settings.model", "SettingsSchema"),
    "SettingsSectionMetadata": (
        ".settings.metadata",
        "SettingsSectionMetadata",
    ),
    "SettingsSectionSchema": (".settings.model", "SettingsSectionSchema"),
    "export_settings_schema": (".settings.model", "export_settings_schema"),
    "field_meta": (".settings.metadata", "field_meta"),
    "FileStore": (".io.base", "FileStore"),
    "LocalFileStore": (".io.local", "LocalFileStore"),
    "Plugin": (".plugin.plugin", "Plugin"),
    "register_tool": (".tool.registry", "register_tool"),
    "resolve_tool": (".tool.registry", "resolve_tool"),
    "list_registered_tools": (".tool.registry", "list_registered_tools"),
    "Workspace": (".workspace.workspace", "Workspace"),
    "LocalWorkspace": (".workspace.local", "LocalWorkspace"),
    "RemoteWorkspace": (".workspace.remote", "RemoteWorkspace"),
    "AsyncRemoteWorkspace": (".workspace.remote", "AsyncRemoteWorkspace"),
    "register_agent": (".subagent.registry", "register_agent"),
    "load_project_agents": (".subagent.load", "load_project_agents"),
    "load_user_agents": (".subagent.load", "load_user_agents"),
    "load_agents_from_dir": (".subagent.load", "load_agents_from_dir"),
    "agent_definition_to_factory": (
        ".subagent.registry",
        "agent_definition_to_factory",
    ),
    "load_project_skills": (".skills.skill", "load_project_skills"),
    "load_skills_from_dir": (".skills.skill", "load_skills_from_dir"),
    "load_user_skills": (".skills.skill", "load_user_skills"),
    "page_iterator": (".utils.paging", "page_iterator"),
}


def __getattr__(name: str) -> Any:
    return import_lazy_symbol(__name__, globals(), _LAZY_IMPORTS, name)


def __dir__() -> list[str]:
    return lazy_dir(globals(), __all__)
