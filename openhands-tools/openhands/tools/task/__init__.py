"""Task tool package for sub-agent delegation.

This package provides a TaskToolSet tool to delegate tasks to subagent.

Tools:
    - task: Launch and run a (blocking) sub-agent task.

Usage:
    from openhands.tools.task import TaskToolSet

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name),
            Tool(name=TaskToolSet.name),
        ],
    )
"""

from openhands.tools.task.definition import TaskToolSet


__all__ = ["TaskToolSet"]
