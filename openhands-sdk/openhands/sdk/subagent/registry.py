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

    # Register the agent with a description
    register_agent(
        name="security_expert",
        factory_func=create_security_expert,
        description="Expert in security analysis and vulnerability assessment"
    )
"""

from collections.abc import Callable
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, NamedTuple

from openhands.sdk.logger import get_logger
from openhands.sdk.subagent.load import (
    load_agents_from_dir,
    load_project_agents,
    load_user_agents,
)
from openhands.sdk.subagent.schema import AgentDefinition


if TYPE_CHECKING:
    from openhands.sdk.agent.agent import Agent
    from openhands.sdk.llm.llm import LLM

logger = get_logger(__name__)

BUILTINS_DIR = Path(__file__).parent / "builtins"


class AgentFactory(NamedTuple):
    """Simple container for an agent factory function and its description."""

    factory_func: Callable[["LLM"], "Agent"]
    description: str


# Global registry for user-registered agent factories
_agent_factories: dict[str, AgentFactory] = {}
_registry_lock = RLock()


def register_agent(
    name: str,
    factory_func: Callable[["LLM"], "Agent"],
    description: str,
) -> None:
    """
    Register a custom agent globally.

    Args:
        name: Unique name for the agent
        factory_func: Function that takes an LLM and returns an Agent
        description: Human-readable description of what this agent does

    Raises:
        ValueError: If an agent with the same name already exists
    """
    with _registry_lock:
        if name in _agent_factories:
            raise ValueError(f"Agent '{name}' already registered")

        _agent_factories[name] = AgentFactory(
            factory_func=factory_func, description=description
        )


def register_agent_if_absent(
    name: str,
    factory_func: Callable[["LLM"], "Agent"],
    description: str,
) -> bool:
    """
    Register a custom agent if no agent with that name exists yet.

    Unlike register_agent(), this does not raise on duplicates. This is used
    by file-based and plugin-based agent loading to gracefully skip conflicts
    with programmatically registered agents.

    Args:
        name: Unique name for the agent
        factory_func: Function that takes an LLM and returns an Agent
        description: Human-readable description of what this agent does

    Returns:
        True if the agent was registered, False if an agent with that name
        already existed.
    """
    with _registry_lock:
        if name in _agent_factories:
            return False

        _agent_factories[name] = AgentFactory(
            factory_func=factory_func, description=description
        )
        return True


def agent_definition_to_factory(
    agent_def: AgentDefinition,
) -> Callable[["LLM"], "Agent"]:
    """Create an agent factory closure from an `AgentDefinition`.

    The returned callable accepts an `LLM` instance (the parent agent's LLM)
    and builds a fully-configured `Agent` instance.

    - Tool names from `agent_def.tools` are mapped to `Tool` objects.
    - The system prompt is set as the `system_message_suffix` on the
      `AgentContext`.
    - `model: inherit` preserves the parent LLM; an explicit model name
      creates a copy via `model_copy(update=...)`.
    """

    def _factory(llm: "LLM") -> "Agent":
        from openhands.sdk.agent.agent import Agent
        from openhands.sdk.context.agent_context import AgentContext
        from openhands.sdk.tool.spec import Tool

        # Handle model override
        if agent_def.model and agent_def.model != "inherit":
            llm = llm.model_copy(update={"model": agent_def.model})

        # the system prompt of the subagent is added as a suffix of the
        # main system prompt
        agent_context = (
            AgentContext(system_message_suffix=agent_def.system_prompt)
            if agent_def.system_prompt
            else None
        )

        # Resolve tools
        tools = [Tool(name=tool_name) for tool_name in agent_def.tools]

        return Agent(
            llm=llm,
            tools=tools,
            agent_context=agent_context,
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
        factory = agent_definition_to_factory(agent_def)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def.description or f"File-based agent: {agent_def.name}",
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(
                f"Registered file-based agent '{agent_def.name}'"
                + (f" from {agent_def.source}" if agent_def.source else "")
            )

    return registered


def register_plugin_agents(agents: list[AgentDefinition]) -> list[str]:
    """Register plugin-provided agent definitions into the delegate registry.

    Plugin agents have higher priority than file-based agents but lower than
    programmatic ``register_agent()`` calls. This function bridges the existing
    ``Plugin.agents`` list (which is loaded but not currently registered) into
    the delegate registry.

    Args:
        agents: Agent definitions collected from loaded plugins.

    Returns:
        List of agent names that were actually registered.
    """
    registered: list[str] = []
    for agent_def in agents:
        factory = agent_definition_to_factory(agent_def)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def.description or f"Plugin agent: {agent_def.name}",
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(f"Registered plugin agent '{agent_def.name}'")

    return registered


def register_builtins_agents() -> list[str]:
    """Load and register SDK builtin agents from ``subagent/builtins/*.md``.

    They are registered via ``register_agent_if_absent`` and will not
    overwrite agents already registered by programmatic calls, plugins,
    or project/user-level file-based definitions.

    Returns:
        List of agent names that were actually registered.
    """
    builtins_agents_def = load_agents_from_dir(BUILTINS_DIR)

    registered: list[str] = []
    for agent_def in builtins_agents_def:
        factory = agent_definition_to_factory(agent_def)
        was_registered = register_agent_if_absent(
            name=agent_def.name,
            factory_func=factory,
            description=agent_def.description or f"Agent: {agent_def.name}",
        )
        if was_registered:
            registered.append(agent_def.name)
            logger.info(
                f"Registered file-based agent '{agent_def.name}'"
                + (f" from {agent_def.source}" if agent_def.source else "")
            )
    return registered


def get_agent_factory(name: str | None) -> AgentFactory:
    """
    Get a registered agent factory by name.

    Args:
        name: Name of the agent factory to retrieve. If None, empty, or "default",
            the default agent factory is returned.

    Returns:
        AgentFactory: The factory function and description

    Raises:
        ValueError: If no agent factory with the given name is found
    """
    if name is None or name == "":
        factory_name = "default"
    else:
        factory_name = name

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

    info_lines = []
    info_lines.append(
        "- **default**: Default general-purpose agent (used when no agent type is provided)"  # noqa: E501
    )

    if not user_factories:
        info_lines.append(
            "- No user-registered agents yet. Call register_agent(...) to add custom agents."  # noqa: E501
        )
        return "\n".join(info_lines)

    for name, factory in sorted(user_factories.items()):
        info_lines.append(f"- **{name}**: {factory.description}")

    return "\n".join(info_lines)


def _reset_registry_for_tests() -> None:
    """Clear the registry for tests to avoid cross-test contamination."""
    with _registry_lock:
        _agent_factories.clear()
