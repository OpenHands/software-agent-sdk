from functools import cache

from openhands.sdk.subagent.registry import AgentFactory, agent_definition_to_factory
from openhands.sdk.subagent.schema import AgentDefinition


@cache
def _build_default_agent_factory(
    enable_browser: bool = True,
) -> AgentFactory:
    """Return an AgentFactory class describing for the default agent.

    Args:
        enable_browser: Whether to include browser tools.

    Returns:
        An AgentFactory class describing the default agent.
    """

    tool_names = ["terminal", "file_editor", "task_tracker"]
    if enable_browser:
        tool_names.append("browser_tool_set")

    agent_def = AgentDefinition(
        name="default",
        description="Default general-purpose agent",
        model="inherit",
        tools=tool_names,
    )
    return AgentFactory(
        factory_func=agent_definition_to_factory(agent_def),
        description=agent_def.description or "Default general-purpose agent",
    )


def get_default_agent(enable_browser: bool = False) -> AgentFactory:
    return _build_default_agent_factory(enable_browser=enable_browser)
