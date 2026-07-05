"""Default preset configuration for OpenHands agents."""

from pathlib import Path

from openhands.sdk import Agent, agent_definition_to_factory, load_agents_from_dir
from openhands.sdk.context.condenser import default_condenser
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.subagent import AgentDefinition, register_agent_if_absent
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
    enable_sub_agents: bool = False,
) -> list[Tool]:
    """Get the default set of tool specifications for the standard experience.

    Args:
        enable_browser: Whether to include browser tools.
        enable_sub_agents: Whether to include the TaskToolSet for
            sub-agent delegation.
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
    if enable_sub_agents:
        from openhands.tools.task import TaskToolSet

        tools.append(Tool(name=TaskToolSet.name))
    return tools


def get_default_condenser(llm: LLM) -> CondenserBase:
    # Shared with spawned sub-agents (see sdk default_condenser) so both stay in sync.
    return default_condenser(llm)


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


def discover_builtin_agents(enable_browser: bool = True) -> list[AgentDefinition]:
    """Load builtin agent definitions (``level='builtin'``) without registering them.

    Non-mutating counterpart to ``register_builtins_agents``. Browser-only agents
    are skipped when ``enable_browser`` is False.

    Args:
        enable_browser: When False, skip agents needing browser tools (web researcher).

    Returns:
        Builtin agent definitions with ``level="builtin"``.
    """
    subagent_dir = Path(__file__).parent / "subagents"
    builtins_agents_def = load_agents_from_dir(subagent_dir)

    # Filter out browser-dependent agents when browser is not available
    if not enable_browser:
        _browser_only_agents = {"web-researcher"}
        builtins_agents_def = [
            agent
            for agent in builtins_agents_def
            if agent.name not in _browser_only_agents
        ]

    return [
        agent_def.model_copy(update={"level": "builtin"})
        for agent_def in builtins_agents_def
    ]


def register_builtins_agents(enable_browser: bool = True) -> list[str]:
    """Load and register builtin agents from ``subagent/*.md``.
    They are registered via `register_agent_if_absent` and will not
    overwrite agents already registered by programmatic calls, plugins,
    or project/user-level file-based definitions.
    Args:
        enable_browser: Whether browser tools are available. When False,
            agents that require browser tools (e.g. web researcher) are
            skipped.
    Returns:
        List of agents which were actually registered.
    """
    register_default_tools(enable_browser=enable_browser)

    builtins_agents_def = discover_builtin_agents(enable_browser=enable_browser)

    registered: list[str] = []
    for agent_def in builtins_agents_def:
        factory = agent_definition_to_factory(agent_def)
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
