"""Claude preset configuration for OpenHands agents.

This preset uses Claude Code-style delegation tools (task, task_output,
task_stop) instead of the default DelegateTool. The file editing tools
remain the default claude-style FileEditorTool.
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


def register_claude_tools(enable_browser: bool = True) -> None:
    """Register the Claude preset tools (including Claude-style delegation)."""
    from openhands.tools.claude import ClaudeDelegationToolSet
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    logger.debug(f"Tool: {TerminalTool.name} registered.")
    logger.debug(f"Tool: {FileEditorTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")
    logger.debug(f"Tool: {ClaudeDelegationToolSet.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_claude_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the Claude preset tool specifications.

    This uses Claude Code-style delegation tools (task, task_output, task_stop)
    along with the standard file editing and terminal tools.

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_claude_tools(enable_browser=enable_browser)

    from openhands.tools.claude import ClaudeDelegationToolSet
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.task_tracker import TaskTrackerTool
    from openhands.tools.terminal import TerminalTool

    tools = [
        Tool(name=TerminalTool.name),
        Tool(name=FileEditorTool.name),
        Tool(name=TaskTrackerTool.name),
        Tool(name=ClaudeDelegationToolSet.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_claude_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser for Claude preset."""
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)
    return condenser


def get_claude_agent(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    """Get an agent with Claude Code-style delegation tools: task, task_output,
    task_stop."""
    tools = get_claude_tools(
        enable_browser=not cli_mode,
    )
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": cli_mode},
        condenser=get_claude_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent
