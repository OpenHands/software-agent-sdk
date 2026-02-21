"""TaskToolSet.

This module provides Claude Code-style delegation tools as an alternative to
the default DelegateTool.

Tools:
    - task: Launch and run a sub-agent task (sync or background)
    - task_output: Get output from a background task
    - task_stop: Stop a running background task

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
