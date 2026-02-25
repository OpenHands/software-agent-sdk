from functools import cache

from openhands.sdk.subagent.registry import AgentFactory, agent_definition_to_factory
from openhands.sdk.subagent.schema import AgentDefinition


@cache
def _build_default_subagent_factory(
    cli_mode: bool = False,
) -> AgentFactory:
    """Return an `AgentFactory` instance for the default agent.

    Args:
        cli_mode: Whether we are in CLI mode.

    Returns:
        An `AgentFactory` instance for the default agent.
    """

    tool_names = ["terminal", "file_editor", "task_tracker"]
    # Add browser tools if not CLI mode
    if not cli_mode:
        tool_names.append("browser_tool_set")

    agent_def = AgentDefinition(
        name="default",
        description="Default general-purpose agent",
        model="inherit",
        tools=tool_names,
    )
    return AgentFactory(
        factory_func=agent_definition_to_factory(agent_def),
        description=agent_def.description,
    )


def get_default_subagent(cli_mode: bool = False) -> AgentFactory:
    """Return the default agent factory.

    In CLI mode browser tools are excluded from the default tool set.

    Args:
        cli_mode: Whether we are in CLI mode.

    Returns:
        An `AgentFactory` instance for the default agent.
    """
    return _build_default_subagent_factory(cli_mode=cli_mode)
