"""Project coordinator tool package.

Provides ``list_project_threads`` and ``message_project_thread``: server-executed
tools that let a conversation see the threads of its agent-server project and
send one of them a follow-up. Together with ``start_child_conversation`` (a
child copies its parent's ``project`` tag) they give a project coordinator
typed actions to start, follow and steer its threads.

Usage:
    from openhands.tools.project import (
        ListProjectThreadsTool,
        MessageProjectThreadTool,
    )

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=StartChildConversationTool.name),
            Tool(name=ListProjectThreadsTool.name),
            Tool(name=MessageProjectThreadTool.name),
        ],
    )

Like ``start_child_conversation``, the tools are not part of the default
preset: they need the agent-server's projects API.
"""

from openhands.tools.project.definition import (
    ListProjectThreadsAction,
    ListProjectThreadsObservation,
    ListProjectThreadsTool,
    MessageProjectThreadAction,
    MessageProjectThreadObservation,
    MessageProjectThreadTool,
    ProjectThreadSummary,
)
from openhands.tools.project.impl import (
    ListProjectThreadsExecutor,
    MessageProjectThreadExecutor,
)


__all__ = [
    "ListProjectThreadsAction",
    "ListProjectThreadsExecutor",
    "ListProjectThreadsObservation",
    "ListProjectThreadsTool",
    "MessageProjectThreadAction",
    "MessageProjectThreadExecutor",
    "MessageProjectThreadObservation",
    "MessageProjectThreadTool",
    "ProjectThreadSummary",
]
