"""Project coordinator tool package.

Provides ``list_project_conversations``, ``message_project_conversation``,
``read_project_conversation`` and ``save_project_artifact``: server-executed
tools that let a conversation see the conversations of its agent-server
project, send one of them a follow-up, read one's latest messages, and save a
file it made to the project's artifacts. Together with
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
    SaveProjectArtifactAction,
    SaveProjectArtifactObservation,
    SaveProjectArtifactTool,
)
from openhands.tools.project.impl import (
    ListProjectConversationsExecutor,
    MessageProjectConversationExecutor,
    ReadProjectConversationExecutor,
    SaveProjectArtifactExecutor,
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
    "SaveProjectArtifactAction",
    "SaveProjectArtifactExecutor",
    "SaveProjectArtifactObservation",
    "SaveProjectArtifactTool",
]
