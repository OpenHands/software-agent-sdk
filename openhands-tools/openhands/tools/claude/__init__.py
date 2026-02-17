"""Claude Code-style delegation tools.

This module provides Claude Code-style delegation tools as an alternative to
the default DelegateTool. These tools match the Task/TaskOutput/TaskStop
interface used by Claude Code.

Tools:
    - task: Launch and run a sub-agent task (sync or background)
    - task_output: Get output from a background task
    - task_stop: Stop a running background task

Usage:
    To use Claude-style delegation tools, add the tool set to your agent::

        from openhands.tools.claude import CLAUDE_DELEGATION_TOOLS

        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
                *CLAUDE_DELEGATION_TOOLS,
            ],
        )

    Or reference the tool set directly::

        from openhands.tools.claude import ClaudeDelegationToolSet

        agent = Agent(
            llm=llm,
            tools=[
                Tool(name=TerminalTool.name),
                Tool(name=ClaudeDelegationToolSet.name),
            ],
        )
"""

from openhands.tools.claude.definition import ClaudeDelegationToolSet


__all__ = ["ClaudeDelegationToolSet"]
