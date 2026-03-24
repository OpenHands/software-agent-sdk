"""Nemotron-3 Super preset configuration for OpenHands agents.

Nemotron-3 Super (nvidia/nemotron-3-super-120b-a12b) was fine-tuned on
trajectories that use the Anthropic str_replace_based_edit_tool / bash
tool schema. This preset exposes those exact tool names so the model's
calls succeed without any prompt engineering or model-side changes.

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


def register_nemotron_tools(enable_browser: bool = True) -> None:
    """Register the nemotron set of tools."""
    from openhands.tools.nemotron import BashTool, StrReplaceTool
    from openhands.tools.task_tracker import TaskTrackerTool

    logger.debug(f"Tool: {BashTool.name} registered.")
    logger.debug(f"Tool: {StrReplaceTool.name} registered.")
    logger.debug(f"Tool: {TaskTrackerTool.name} registered.")

    if enable_browser:
        from openhands.tools.browser_use import BrowserToolSet

        logger.debug(f"Tool: {BrowserToolSet.name} registered.")


def get_nemotron_tools(
    enable_browser: bool = True,
) -> list[Tool]:
    """Get the nemotron set of tool specifications.

    This uses Anthropic-compatible tool names (bash, str_replace) instead
    of the default OpenHands names (terminal, file_editor).

    Args:
        enable_browser: Whether to include browser tools.
    """
    register_nemotron_tools(enable_browser=enable_browser)

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


def get_nemotron_condenser(llm: LLM) -> CondenserBase:
    """Get the default condenser for nemotron preset."""
    condenser = LLMSummarizingCondenser(llm=llm, max_size=80, keep_first=4)
    return condenser


def get_nemotron_agent(
    llm: LLM,
    cli_mode: bool = False,
) -> Agent:
    """Get an agent with Nemotron-compatible tools: bash, str_replace.

    Args:
        llm: The LLM to use for the agent.
        cli_mode: Whether to run in CLI mode (disables browser tools).
    """
    tools = get_nemotron_tools(
        enable_browser=not cli_mode,
    )
    agent = Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": cli_mode},
        condenser=get_nemotron_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )
    return agent
