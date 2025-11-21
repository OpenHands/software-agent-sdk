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
from typing import NamedTuple

from openhands.sdk import LLM, Agent
from openhands.tools.preset.default import get_default_agent


class AgentFactory(NamedTuple):
    """Simple container for an agent factory function and its description."""

    factory_func: Callable[[LLM], Agent]
    description: str


# Global registry for user-registered agent factories
_agent_factories: dict[str, AgentFactory] = {}


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
    if name in _agent_factories:
        raise ValueError(f"Agent '{name}' already registered")

    _agent_factories[name] = AgentFactory(
        factory_func=factory_func, description=description
    )


def get_agent_factory(name: str) -> AgentFactory:
    """
    Get a registered agent factory by name.

    Args:
        name: Name of the agent factory to retrieve

    Returns:
        AgentFactory: The factory function and description

    Raises:
        ValueError: If no agent factory with the given name is found
    """
    if name not in _agent_factories:
        available = ", ".join(sorted(_agent_factories.keys()))
        raise ValueError(f"Unknown agent '{name}'. Available: {available}")

    return _agent_factories[name]


def get_factory_info() -> str:
    """Get formatted information about available agent factories."""
    has_user_factories = bool(_agent_factories)

    if not has_user_factories:
        return "No specialized agent factories available."

    info_lines = ["Available user-registered agent factories:"]
    for name, factory in sorted(_agent_factories.items()):
        info_lines.append(f"- **{name}**: {factory.description}")

    info_lines.append("")
    info_lines.append(
        "If no agent is specified, a default general-purpose agent will be created."
    )

    return "\n".join(info_lines)


def create_default_agent(llm: LLM) -> Agent:
    """Create a default general-purpose agent."""
    return get_default_agent(llm)


register_agent(
    name="default",
    factory_func=create_default_agent,
    description="Default general-purpose agent",
)
