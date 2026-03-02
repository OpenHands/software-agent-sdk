"""Default preset configuration for OpenHands agents."""

from pathlib import Path

from openhands.sdk import Agent, agent_definition_to_factory, load_agents_from_dir
from openhands.sdk.context.condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.subagent import register_agent_if_absent
from openhands.sdk.tool import Tool


logger = get_logger(__name__)


def register_default_tools(enable_browser: bool = True) -> None:
    """Register the default set of tools."""
    # Tools are now automatically registered when imported
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    logger.debug(f"Tool: {TerminalTool.name} registered.")
    logger.debug(f"Tool: {FileEditorTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_default_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the default set of tool specifications for the standard experience.

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_default_tools(enable_browser=enable_browser)

    # Import tools to access their name attributes
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_default_condenser(llm: LLM) -> CondenserBase:
    # Create a condenser to manage the context. The condenser will automatically
    # truncate conversation history when it exceeds max_size, and replaces the dropped
    # events with an LLM-generated summary.
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)

    return condenser


def get_default_agent(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    tools = get_default_tools(
        # Disable browser tools in CLI mode
        enable_browser=not cli_mode,
    )
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": cli_mode},
        condenser=get_default_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent


def register_builtins_agents(cli_mode: bool = False) -> list[str]:
    """Load and register builtin agents from ``subagent/*.md``.

    They are registered via `register_agent_if_absent` and will not
    overwrite agents already registered by programmatic calls, plugins,
    or project/user-level file-based definitions.

    Args:
        cli_mode: Whether to load the default agent in cli mode or not.

    Returns:
        List of agents which were actually registered.
    """
    register_default_tools(
        # Disable browser tools in CLI mode
        enable_browser=not cli_mode,
    )

    subagent_dir = Path(__file__).parent / "subagents"
    builtins_agents_def = load_agents_from_dir(subagent_dir)

    # if we are in cli mode, we filter out the default agent (with browser tool)
    # otherwise, we filter out the default cli agent
    if cli_mode:
        builtins_agents_def = [
            agent for agent in builtins_agents_def if agent.name != "default"
        ]
    else:
        builtins_agents_def = [
            agent for agent in builtins_agents_def if agent.name != "default cli mode"
        ]

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
