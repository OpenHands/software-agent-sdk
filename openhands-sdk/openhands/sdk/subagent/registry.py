"""
Simple API for users to register custom agents.

Example usage:
    from openhands.sdk import register_agent, Agent, AgentContext
    from openhands.sdk.tool.spec import Tool

    # Define a custom security expert factory
    def create_security_expert(llm):
        tools = [Tool(name="TerminalTool")]
        agent_context = AgentContext(
            system_message_suffix=(
                "You are a cybersecurity expert. Always consider security implications."
            ),
        )
        return Agent(llm=llm, tools=tools, agent_context=agent_context)

    # Register with a plain description (local-only, no remote metadata)
    register_agent(
        name="security_expert",
        factory_func=create_security_expert,
        description="Expert in security analysis and vulnerability assessment",
    )
"""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, NamedTuple

from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.logger import get_logger
from openhands.sdk.subagent.load import (
    load_project_agents,
    load_user_agents,
)
from openhands.sdk.subagent.schema import AgentDefinition
from openhands.sdk.utils.deprecation import warn_deprecated


if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.llm.llm import LLM

logger = get_logger(__name__)


class AgentFactory(NamedTuple):
    """Container for an agent factory function and its definition."""

    factory_func: Callable[["LLM"], "Agent"]
    definition: AgentDefinition


# Global registry for user-registered agent factories
_agent_factories: dict[str, AgentFactory] = {}
_registry_lock = RLock()


def _resolve_agent_definition(
    name: str,
    description: str | AgentDefinition,
) -> AgentDefinition:
    """Build or normalise an `AgentDefinition` for registration.

    When description is a plain string a minimal definition is created
    from name and description.  When it is already an
    `AgentDefinition` it is returned as-is.

    Args:
        name: Agent name used as the registry key.
        description: Either a human-readable description string (a minimal
            `AgentDefinition` will be created) or a full
            `AgentDefinition` instance.

    Returns:
        An `AgentDefinition` ready for storage.
    """
    if isinstance(description, AgentDefinition):
        return description
    return AgentDefinition(name=name, description=description)


def register_agent(
    name: str,
    factory_func: Callable[["LLM"], "Agent"],
    description: str | AgentDefinition,
) -> None:
    """Register a custom agent globally.

    The factory_func is the source of truth for local execution —
    it receives an `LLM` and must return a fully-configured `Agent`.

    The description parameter accepts either a plain string or a full
    `AgentDefinition`.  A plain string creates a minimal definition
    from name and description; this is fine for local-only agents but
    means the remote server will not know about tools or system prompts.
    Pass an `AgentDefinition` when the agent needs to work in remote
    workspaces, as the definition's metadata (tools, system_prompt,
    model, skills, …) is serialised and forwarded to the agent-server.

    Args:
        name: Unique name for the agent (used as the registry key).
        factory_func: Function that takes an LLM and returns an Agent.
        description: A human-readable description string, or a full
            `AgentDefinition` carrying tools, system_prompt, model,
            and other metadata needed for remote execution.

    Raises:
        ValueError: If an agent with the same name already exists.
    """
    definition = _resolve_agent_definition(name, description)

    with _registry_lock:
        if name in _agent_factories:
            raise ValueError(f"Agent '{name}' already registered")

        _agent_factories[name] = AgentFactory(
            factory_func=factory_func, definition=definition
        )


def register_agent_if_absent(
    name: str,
    factory_func: Callable[["LLM"], "Agent"],
    description: str | AgentDefinition,
) -> bool:
    """Register a custom agent if no agent with that name exists yet.

    Behaves identically to `register_agent` except that it silently
    no-ops when an agent with *name* is already registered, instead of
    raising `ValueError`.  This is used by file-based and plugin-based
    agent loading to gracefully skip conflicts with programmatically
    registered agents.

    See `register_agent` for full parameter documentation.

    Returns:
        `True` if the agent was registered, `False` if an agent with
        that name already existed.
    """
    definition = _resolve_agent_definition(name, description)

    with _registry_lock:
        if name in _agent_factories:
            return False

        _agent_factories[name] = AgentFactory(
            factory_func=factory_func, definition=definition
        )
        return True


@lru_cache(maxsize=32)
def _get_profile_store(profile_store_dir: str | None) -> LLMProfileStore:
    return LLMProfileStore(profile_store_dir)


def agent_definition_to_factory(
    agent_def: AgentDefinition,
    work_dir: str | Path | None = None,
) -> Callable[["LLM"], "Agent"]:
    """Create an agent factory closure from an `AgentDefinition`.

    The returned callable accepts the parent agent's LLM and produces a
    fully-configured `Agent`.

    - Tool names from `agent_def.tools` are mapped to `Tool` objects.
    - Skill names from `agent_def.skills` are resolved to `Skill` objects
      from project and user skill directories (project takes priority).
    - The system prompt is set as the `system_message_suffix` on the
      `AgentContext`.
    - `model: inherit` preserves the parent LLM; an explicit model name
      creates a copy via `model_copy(update=...)`.

    Note: Callers (e.g. DelegateTool, TaskManager) are responsible for
    disabling streaming and resetting metrics on the resulting agent's LLM.

    Args:
        agent_def: The agent definition to convert.
        work_dir: Project directory for resolving skill names. If None,
            only user-level skills are searched.

    Raises:
        ValueError: If a tool or skill is not found.
    """
    # Resolve skills eagerly at factory creation time.
    # Priority: project skills override user skills (handled by load_available_skills).
    resolved_skills: list = []
    if agent_def.skills:
        from openhands.sdk.context.skills import load_available_skills

        available = load_available_skills(
            work_dir, include_user=True, include_project=True, include_public=False
        )

        for name in agent_def.skills:
            if name not in available:
                raise ValueError(
                    f"Skill '{name}' not found but was given to agent "
                    f"'{agent_def.name}'."
                )
            resolved_skills.append(available[name])

    def _factory(llm: "LLM") -> "Agent":
        from openhands.sdk.agent.agent import Agent
        from openhands.sdk.context.agent_context import AgentContext
        from openhands.sdk.tool.registry import list_registered_tools
        from openhands.sdk.tool.spec import Tool

        # Load LLM profile if agent_def.model is different from
        # 'inherit' and empty string
        if agent_def.model and agent_def.model != "inherit":
            store = _get_profile_store(agent_def.profile_store_dir)
            available_profiles = [name.removesuffix(".json") for name in store.list()]
            profile_name = agent_def.model.removesuffix(".json")
            if profile_name not in available_profiles:
                raise ValueError(
                    f"Profile {agent_def.model} not found in profile store.\n"
                    f"Available profiles: {available_profiles}"
                )

            llm = store.load(profile_name)

        # the system prompt of the subagent is added as a suffix of the
        # main system prompt
        has_context = agent_def.system_prompt or resolved_skills
        agent_context = (
            AgentContext(
                system_message_suffix=agent_def.system_prompt or None,
                skills=resolved_skills,
            )
            if has_context
            else None
        )

        # Resolve tools
        tools: list[Tool] = []
        registered_tools: set[str] = set(list_registered_tools())
        for tool_name in agent_def.tools:
            if tool_name not in registered_tools:
                raise ValueError(
                    f"Tool '{tool_name}' not registered"
                    f"but was given to agent {agent_def.name}."
                )
            tools.append(Tool(name=tool_name))

        # Build MCP config if servers are defined.
        # Key is "mcpServers" (camelCase) to match the MCPConfig schema
        # (see sdk/plugin/types.py McpServersDict alias and Agent.mcp_config examples).
        mcp_config: dict[str, Any] = {}
        if agent_def.mcp_servers:
            mcp_config = {"mcpServers": agent_def.mcp_servers}

        return Agent(
            llm=llm,
            tools=tools,
            agent_context=agent_context,
            mcp_config=mcp_config,
        )

    return _factory


def register_file_agents(work_dir: str | Path) -> list[str]:
    """Load and register file-based agents from project-level `.agents/agents` and
    `.openhands/agents`, and user-level `~/.agents/agents` and `~/.openhands/agents`
    directories.

    Project-level definitions take priority over user-level ones, and within
    each level `.agents/` takes priority over `.openhands/`.

    Does not overwrite agents already registered programmatically or by plugins.

    Returns:
        List of agent names that were actually registered.
    """
    project_agents = load_project_agents(work_dir)
    user_agents = load_user_agents()

    # Deduplicate: project wins over user
    seen_names: set[str] = set()
    deduplicated: list[AgentDefinition] = []

    for agent_def in project_agents:
        if agent_def.name not in seen_names:
            seen_names.add(agent_def.name)
            deduplicated.append(agent_def)

    for agent_def in user_agents:
        if agent_def.name not in seen_names:
            seen_names.add(agent_def.name)
            deduplicated.append(agent_def)

    registered: list[str] = []
    for agent_def in deduplicated:
        factory = agent_definition_to_factory(agent_def, work_dir=work_dir)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def,
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(
                f"Registered file-based agent '{agent_def.name}'"
                + (f" from {agent_def.source}" if agent_def.source else "")
            )

    return registered


def register_plugin_agents(
    agents: list[AgentDefinition],
    work_dir: str | Path | None = None,
) -> list[str]:
    """Register plugin-provided agent definitions into the delegate registry.

    Plugin agents have higher priority than file-based agents but lower than
    programmatic ``register_agent()`` calls. This function bridges the existing
    ``Plugin.agents`` list (which is loaded but not currently registered) into
    the delegate registry.

    Args:
        agents: Agent definitions collected from loaded plugins.
        work_dir: Project directory for resolving skill names in agent
            definitions. If None, only user-level skills are searched.

    Returns:
        List of agent names that were actually registered.
    """
    registered: list[str] = []
    for agent_def in agents:
        factory = agent_definition_to_factory(agent_def, work_dir=work_dir)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def,
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(f"Registered plugin agent '{agent_def.name}'")

    return registered


def get_agent_factory(name: str | None) -> AgentFactory:
    """
    Get a registered agent factory by name.

    Args:
        name: Name of the agent factory to retrieve. If None, empty, or "default",
            the default agent factory is returned.

    Returns:
        AgentFactory: The factory function and definition

    Raises:
        ValueError: If no agent factory with the given name is found
    """
    # Map old names to new names for backward compatibility
    _DEPRECATED_NAMES = {
        "default": "general-purpose",
        "default cli mode": "general-purpose",
        "explore": "code-explorer",
        "bash": "bash-runner",
    }

    if name in _DEPRECATED_NAMES:
        new_name = _DEPRECATED_NAMES[name]
        warn_deprecated(
            f"Agent name '{name}'",
            deprecated_in="1.12.0",
            removed_in="2.0.0",
            details=f"Use '{new_name}' instead.",
        )
        factory_name = new_name
    else:
        factory_name = "general-purpose" if not name else name

    with _registry_lock:
        factory = _agent_factories.get(factory_name)
        available = sorted(_agent_factories.keys())

    if factory is None:
        available_list = ", ".join(available) if available else "none registered"
        raise ValueError(
            f"Unknown agent '{name}'. Available types: {available_list}. "
            "Use register_agent() to add custom agent types."
        )

    return factory


def get_factory_info() -> str:
    """Get formatted information about available agent factories."""
    with _registry_lock:
        user_factories = dict(_agent_factories)

    if not user_factories:
        return "- No user-registered agents yet. Call register_agent(...) to add custom agents."  # noqa: E501

    def get_agent_info(name, factory):
        defn = factory.definition
        tools = f" (tools: {', '.join(defn.tools)})" if defn.tools else ""
        return f"- **{name}**: {defn.description}{tools}"

    return "\n".join(
        get_agent_info(name, f) for name, f in sorted(user_factories.items())
    )


def get_registered_agent_definitions() -> list[AgentDefinition]:
    """Return the definitions of all registered agents.

    Useful for forwarding agent metadata to a remote agent-server.
    """
    with _registry_lock:
        return [f.definition for f in _agent_factories.values()]


def _reset_registry_for_tests() -> None:
    """Clear the registry for tests to avoid cross-test contamination."""
    with _registry_lock:
        _agent_factories.clear()
