"""Gemini preset configuration for OpenHands agents.

This preset uses gemini-style file editing tools instead of the default
claude-style file_editor tool.
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


def register_gemini_tools(enable_browser: bool = True) -> None:
    """Register the gemini set of tools."""
    from openhands.tools.gemini import (
        EditTool,
        ListDirectoryTool,
        ReadFileTool,
        WriteFileTool,
    )
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    logger.debug(f"Tool: {TerminalTool.name} registered.")
    logger.debug(f"Tool: {ReadFileTool.name} registered.")
    logger.debug(f"Tool: {WriteFileTool.name} registered.")
    logger.debug(f"Tool: {EditTool.name} registered.")
    logger.debug(f"Tool: {ListDirectoryTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_gemini_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the gemini set of tool specifications.

    This uses gemini-style file editing tools (read_file, write_file, edit,
    list_directory) instead of the default claude-style file_editor tool.

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_gemini_tools(enable_browser=enable_browser)

    from openhands.tools.gemini import (
        EditTool,
        ListDirectoryTool,
        ReadFileTool,
        WriteFileTool,
    )
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=ReadFileTool.name),
        Tool(name=WriteFileTool.name),
        Tool(name=EditTool.name),
        Tool(name=ListDirectoryTool.name),
        Tool(name=TaskTrackerTool.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_gemini_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser for gemini preset."""
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)
    return condenser


def get_gemini_agent(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    """Get an agent with gemini-style tools: read_file, write_file, edit,
    list_directory."""
    tools = get_gemini_tools(
        enable_browser=not cli_mode,
    )
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": cli_mode},
        condenser=get_gemini_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent
