"""Qwen preset configuration for OpenHands agents.

Qwen 3.5 Flash and similar models use the Anthropic str_replace_based_edit_tool
schema (same as Nemotron). This preset exposes tools under the names the model expects.

  bash        → BashExecutor        (model calls "bash", not "terminal")
  str_replace → StrReplaceExecutor  (model calls "str_replace", not "file_editor")
  task_tracker, finish, think — unchanged; model already calls these correctly.
"""

from openhands.sdk import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.context.condenser.base import CondenserBase
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool


logger = get_logger(__name__)


def register_qwen_tools(enable_browser: bool = True) -> None:
    """Register the qwen set of tools.

    Qwen uses the same tool names as Nemotron (Anthropic schema),
    so we reuse the Nemotron tools.
    """
    from openhands.tools.nemotron import BashTool, StrReplaceTool
    from openhands.tools.task_tracker import TaskTrackerTool

    logger.debug(f"Tool: {BashTool.name} registered.")
    logger.debug(f"Tool: {StrReplaceTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_qwen_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the qwen set of tool specifications.

    This uses Anthropic-compatible tool names (bash, str_replace) instead
    of the default OpenHands names (terminal, file_editor).

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_qwen_tools(enable_browser=enable_browser)

    from openhands.tools.nemotron import BashTool, StrReplaceTool
    from openhands.tools.task_tracker import TaskTrackerTool

    tools = [
        Tool(name=BashTool.name),
        Tool(name=StrReplaceTool.name),
        Tool(name=TaskTrackerTool.name),
    ]
    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        tools.append(Tool(name=BrowserToolSet.name))
    return tools


def get_qwen_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser for qwen preset."""
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)
    return condenser


def get_qwen_agent(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    """Get an agent with Qwen-compatible tools: bash, str_replace.

    Args:
        llm: The LLM to use for the agent.
        cli_mode: Whether to run in CLI mode (disables browser tools).
    """
    tools = get_qwen_tools(
        enable_browser=not cli_mode,
    )
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": cli_mode},
        condenser=get_qwen_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent
