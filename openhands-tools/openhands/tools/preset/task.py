"""Preset configuration for OpenHands agent with task tool.

This preset uses task delegation tools (task, task_output,
task_stop) instead of the default DelegateTool, along with
the standard file editing and terminal tools.
"""

from openhands.sdk import Agent
from openhands.sdk.context.condenser import (
    LLMSummarizingCondenser,
)
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool


logger = get_logger(__name__)


def register_agent_with_task_tool(enable_browser: bool = True) -> None:
    """Register an OpenHands agent with task tool."""
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task import TaskToolSet
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    logger.debug(f"Tool: {TerminalTool.name} registered.")
    logger.debug(f"Tool: {FileEditorTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")
    logger.debug(f"Tool: {TaskToolSet.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the preset tool specifications for OpenHands agent with task tool.

    This uses Claude Code-style delegation tools (task, task_output, task_stop)
    along with the standard file editing and terminal tools.

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_agent_with_task_tool(enable_browser=enable_browser)

    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task import TaskToolSet
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
        Tool(name=TaskToolSet.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_default_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser."""
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)
    return condenser


def get_agent_with_task_tool(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    """Get an agent with task tools: task, task_output,
    task_stop."""
    tools = get_tools(
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
