from openhands.sdk.subagent.load import (
    discover_agents,
    load_agents_from_dir,
    load_project_agents,
    load_user_agents,
)
from openhands.sdk.subagent.registry import (
    agent_definition_to_factory,
    get_agent_factory,
    get_factory_info,
    get_registered_agent_definitions,
    register_agent,
    register_agent_if_absent,
    register_file_agents,
    register_plugin_agents,
)
from openhands.sdk.subagent.schema import AgentDefinition, AgentDefinitionLevel
from openhands.sdk.subagent.section_parser import (
    ALWAYS_ACTIVE_SENTINEL,
    parse_sections,
    parse_xml_sections,
)


__all__ = [
    # loading
    "load_user_agents",
    "load_project_agents",
    "load_agents_from_dir",
    "discover_agents",
    # agent registration
    "register_agent",
    "register_file_agents",
    "register_plugin_agents",
    "register_agent_if_absent",
    "get_factory_info",
    "get_agent_factory",
    "get_registered_agent_definitions",
    # Agent def and factory
    "AgentDefinition",
    "AgentDefinitionLevel",
    "agent_definition_to_factory",
    # SCA section parser
    "parse_sections",
    "parse_xml_sections",
    "ALWAYS_ACTIVE_SENTINEL",
]
