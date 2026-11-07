"""Project coordinator tool package.

Provides ``list_project_threads``, ``message_project_thread`` and
``save_project_artifact``: server-executed tools that let a conversation see
the threads of its agent-server project, send one of them a follow-up, and
save a file it made to the project's artifacts. Together with
``start_child_conversation`` (a child copies its parent's ``project`` tag) they
give a project coordinator typed actions to start, follow and steer its
threads.

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
    SaveProjectArtifactAction,
    SaveProjectArtifactObservation,
    SaveProjectArtifactTool,
)
from openhands.tools.project.impl import (
    ListProjectThreadsExecutor,
    MessageProjectThreadExecutor,
    SaveProjectArtifactExecutor,
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
    "SaveProjectArtifactAction",
    "SaveProjectArtifactExecutor",
    "SaveProjectArtifactObservation",
    "SaveProjectArtifactTool",
]
