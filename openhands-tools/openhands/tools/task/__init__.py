"""Task tool package for sub-agent delegation.

This package provides a TaskToolSet for managed sub-agent tasks.

Tools:
    - task: Launch a blocking or background sub-agent task.
    - task_output: Poll or wait for a background task's result.
    - task_stop: Cooperatively stop a background task.

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

from openhands.tools.task.definition import (
    TaskAction,
    TaskObservation,
    TaskOutputAction,
    TaskOutputObservation,
    TaskOutputTool,
    TaskStopAction,
    TaskStopObservation,
    TaskStopTool,
    TaskTool,
    TaskToolSet,
)
from openhands.tools.task.impl import TaskExecutor


__all__ = [
    "TaskAction",
    "TaskExecutor",
    "TaskObservation",
    "TaskOutputAction",
    "TaskOutputObservation",
    "TaskOutputTool",
    "TaskStopAction",
    "TaskStopObservation",
    "TaskStopTool",
    "TaskTool",
    "TaskToolSet",
]
