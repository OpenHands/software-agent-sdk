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

from openhands.tools.task.definition import (
    TaskAction,
    TaskObservation,
    TaskTool,
    TaskToolSet,
)
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.scope import (
    TaskScope,
    TaskScopeAnalysis,
    TaskScopeConflict,
    TaskScopeConflictKind,
    TaskScopeDecision,
    analyze_task_scopes,
)


__all__ = [
    "TaskAction",
    "TaskExecutor",
    "TaskObservation",
    "TaskScope",
    "TaskScopeAnalysis",
    "TaskScopeConflict",
    "TaskScopeConflictKind",
    "TaskScopeDecision",
    "TaskTool",
    "TaskToolSet",
    "analyze_task_scopes",
]
