"""Project coordinator tool package.

Provides ``list_project_conversations``, ``message_project_conversation``,
``read_project_conversation``, ``save_project_artifact``,
``read_project_memory`` and ``update_project_memory``: server-executed tools
that let a conversation see the conversations of its agent-server project,
send one of them a follow-up, read one's latest messages, save a file it made
to the project's artifacts, and read and keep the project's shared memory. Together with
``start_child_conversation`` (a child copies its parent's ``project`` tag) they
give a project coordinator typed actions to start, follow and steer its
conversations.

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
    ListProjectConversationsAction,
    ListProjectConversationsObservation,
    ListProjectConversationsTool,
    MessageProjectConversationAction,
    MessageProjectConversationObservation,
    MessageProjectConversationTool,
    ProjectConversationSummary,
    ReadProjectConversationAction,
    ReadProjectConversationObservation,
    ReadProjectConversationTool,
    ReadProjectMemoryAction,
    ReadProjectMemoryObservation,
    ReadProjectMemoryTool,
    SaveProjectArtifactAction,
    SaveProjectArtifactObservation,
    SaveProjectArtifactTool,
    UpdateProjectMemoryAction,
    UpdateProjectMemoryObservation,
    UpdateProjectMemoryTool,
)
from openhands.tools.project.impl import (
    ListProjectConversationsExecutor,
    MessageProjectConversationExecutor,
    ReadProjectConversationExecutor,
    ReadProjectMemoryExecutor,
    SaveProjectArtifactExecutor,
    UpdateProjectMemoryExecutor,
)


__all__ = [
    "ListProjectConversationsAction",
    "ListProjectConversationsExecutor",
    "ListProjectConversationsObservation",
    "ListProjectConversationsTool",
    "MessageProjectConversationAction",
    "MessageProjectConversationExecutor",
    "MessageProjectConversationObservation",
    "MessageProjectConversationTool",
    "ProjectConversationSummary",
    "ReadProjectConversationAction",
    "ReadProjectConversationExecutor",
    "ReadProjectConversationObservation",
    "ReadProjectConversationTool",
    "ReadProjectMemoryAction",
    "ReadProjectMemoryExecutor",
    "ReadProjectMemoryObservation",
    "ReadProjectMemoryTool",
    "SaveProjectArtifactAction",
    "SaveProjectArtifactExecutor",
    "SaveProjectArtifactObservation",
    "SaveProjectArtifactTool",
    "UpdateProjectMemoryAction",
    "UpdateProjectMemoryExecutor",
    "UpdateProjectMemoryObservation",
    "UpdateProjectMemoryTool",
]
