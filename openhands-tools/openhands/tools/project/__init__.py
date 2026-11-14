"""Project coordinator tool package.

Provides ``list_project_conversations``, ``message_project_conversation``,
``read_project_conversation``, ``write_artifact``, ``read_artifact``,
``list_artifacts``, ``read_project_memory``, ``update_project_memory`` and
``resolve_conversation``: server-executed tools that let a conversation see the
conversations of its agent-server project, send one of them a follow-up, read
one's latest messages, write, list and read the project's artifacts (files),
keep the project's shared memory, and resolve itself when nothing is left in
it. Together with ``start_child_conversation``
(a child copies its parent's ``project`` tag) they give a project coordinator
typed actions to start, follow and steer its conversations.

Usage:
    from openhands.tools.project import (
        ListProjectConversationsTool,
        MessageProjectConversationTool,
    )

    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=StartChildConversationTool.name),
            Tool(name=ListProjectConversationsTool.name),
            Tool(name=MessageProjectConversationTool.name),
        ],
    )

Like ``start_child_conversation``, the tools are not part of the default
preset: they need the agent-server's projects API.
"""

from openhands.tools.project.definition import (
    ListArtifactsAction,
    ListArtifactsObservation,
    ListArtifactsTool,
    ListProjectConversationsAction,
    ListProjectConversationsObservation,
    ListProjectConversationsTool,
    MessageProjectConversationAction,
    MessageProjectConversationObservation,
    MessageProjectConversationTool,
    ProjectConversationSummary,
    ReadArtifactAction,
    ReadArtifactObservation,
    ReadArtifactTool,
    ReadProjectConversationAction,
    ReadProjectConversationObservation,
    ReadProjectConversationTool,
    ReadProjectMemoryAction,
    ReadProjectMemoryObservation,
    ReadProjectMemoryTool,
    ResolveConversationAction,
    ResolveConversationObservation,
    ResolveConversationTool,
    UpdateProjectMemoryAction,
    UpdateProjectMemoryObservation,
    UpdateProjectMemoryTool,
    WriteArtifactAction,
    WriteArtifactObservation,
    WriteArtifactTool,
)
from openhands.tools.project.impl import (
    ListArtifactsExecutor,
    ListProjectConversationsExecutor,
    MessageProjectConversationExecutor,
    ReadArtifactExecutor,
    ReadProjectConversationExecutor,
    ReadProjectMemoryExecutor,
    ResolveConversationExecutor,
    UpdateProjectMemoryExecutor,
    WriteArtifactExecutor,
)


__all__ = [
    "ListArtifactsAction",
    "ListArtifactsExecutor",
    "ListArtifactsObservation",
    "ListArtifactsTool",
    "ListProjectConversationsAction",
    "ListProjectConversationsExecutor",
    "ListProjectConversationsObservation",
    "ListProjectConversationsTool",
    "MessageProjectConversationAction",
    "MessageProjectConversationExecutor",
    "MessageProjectConversationObservation",
    "MessageProjectConversationTool",
    "ProjectConversationSummary",
    "ReadArtifactAction",
    "ReadArtifactExecutor",
    "ReadArtifactObservation",
    "ReadArtifactTool",
    "ReadProjectConversationAction",
    "ReadProjectConversationExecutor",
    "ReadProjectConversationObservation",
    "ReadProjectConversationTool",
    "ReadProjectMemoryAction",
    "ReadProjectMemoryExecutor",
    "ReadProjectMemoryObservation",
    "ReadProjectMemoryTool",
    "ResolveConversationAction",
    "ResolveConversationExecutor",
    "ResolveConversationObservation",
    "ResolveConversationTool",
    "UpdateProjectMemoryAction",
    "UpdateProjectMemoryExecutor",
    "UpdateProjectMemoryObservation",
    "UpdateProjectMemoryTool",
    "WriteArtifactAction",
    "WriteArtifactExecutor",
    "WriteArtifactObservation",
    "WriteArtifactTool",
]
