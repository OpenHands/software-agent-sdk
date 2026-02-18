"""Claude Code-style delegation tools.

This module provides Claude Code-style delegation tools as an alternative to
the default DelegateTool.

Tools:
    - task: Launch and run a sub-agent task (sync or background)
    - task_output: Get output from a background task
    - task_stop: Stop a running background task

Usage:

    from openhands.tools.claude import TaskDelegationToolSet

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=TaskDelegationToolSet.name),
        ],
    )
"""

from openhands.tools.claude.definition import TaskDelegationToolSet


__all__ = ["TaskDelegationToolSet"]
