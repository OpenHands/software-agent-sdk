"""Critic agent preset configuration."""

from openhands.sdk import Agent
from openhands.sdk.context.condenser import LLMSummarizingCondenser
from openhands.sdk.llm.llm import LLM
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import Tool


logger = get_logger(__name__)


def register_critic_tools() -> None:
    """Register the critic agent tools."""
    from openhands.tools.file_editor import FileEditorTool  # noqa: F401
    from openhands.tools.glob import GlobTool  # noqa: F401
    from openhands.tools.grep import GrepTool  # noqa: F401

    logger.debug("Tool: GlobTool registered.")
    logger.debug("Tool: GrepTool registered.")
    logger.debug("Tool: FileEditorTool registered.")


def get_critic_tools() -> list[Tool]:
    """Get the critic agent tool specifications.

    Returns:
        List of tools for code review tasks, including file viewing
        and search capabilities.
    """
    register_critic_tools()

    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.glob import GlobTool
    from openhands.tools.grep import GrepTool

    return [
        Tool(name=GlobTool.name),
        Tool(name=GrepTool.name),
        Tool(name=FileEditorTool.name),
    ]


def get_critic_condenser(llm: LLM) -> LLMSummarizingCondenser:
    """Get a condenser optimized for critic workflows.

    Args:
        llm: The LLM to use for condensation.

    Returns:
        A condenser configured for critic agent needs.
    """
    return LLMSummarizingCondenser(
        llm=llm,
        max_size=50,
        keep_first=4,
    )


def get_critic_agent(llm: LLM) -> Agent:
    """Get a configured critic agent for code review.

    This creates an agent with file viewing and search tools,
    suitable for reviewing code changes and git diffs.

    Args:
        llm: The LLM to use for the critic agent.

    Returns:
        A fully configured critic agent with file operations
        for comprehensive code review.
    """
    tools = get_critic_tools()

    agent = Agent(
        llm=llm.model_copy(update={"usage_id": "critic_agent"}),
        tools=tools,
        condenser=get_critic_condenser(
            llm=llm.model_copy(update={"usage_id": "critic_condenser"})
        ),
    )

    return agent
