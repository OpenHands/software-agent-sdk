"""
Simple API for users to register custom agents.

Example usage:
    from openhands.tools.delegate import register_agent, Skill

    # Define a custom security expert factory
    def create_security_expert(llm):
        tools = [Tool(name="TerminalTool")]
        skills = [Skill(
            name="security_expertise",
            content=(
                "You are a cybersecurity expert. Always consider security implications."
            ),
            trigger=None
        )]
        agent_context = AgentContext(skills=skills)
        return Agent(llm=llm, tools=tools, agent_context=agent_context)

    # Register the agent with a description
    register_agent(
        name="security_expert",
        factory_func=create_security_expert,
        description="Expert in security analysis and vulnerability assessment"
    )
"""

from collections.abc import Callable
from threading import RLock
from typing import NamedTuple

from openhands.sdk import LLM, Agent
from openhands.tools.preset.default import get_default_agent


class AgentFactory(NamedTuple):
    """Simple container for an agent factory function and its description."""

    factory_func: Callable[[LLM], Agent]
    description: str


# Global registry for user-registered agent factories
_agent_factories: dict[str, AgentFactory] = {}
_registry_lock = RLock()


_DEFAULT_FACTORY = AgentFactory(
    factory_func=get_default_agent,
    description="Default general-purpose agent",
)


def register_agent(
    name: str,
    factory_func: Callable[[LLM], Agent],
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
    if name is None or name == "" or name == "default":
        return _DEFAULT_FACTORY

    with _registry_lock:
        factory = _agent_factories.get(name)
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

    info_lines = ["Available agent factories:"]
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
