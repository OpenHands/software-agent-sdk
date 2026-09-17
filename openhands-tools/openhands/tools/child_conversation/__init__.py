"""Child-conversation tool package.

Provides the ``start_child_conversation`` tool: a server-executed tool that
starts a child conversation on the same backend as its parent and returns the
child's id and launch status to the parent agent. The launch itself is
delegated over HTTP to a child-launch endpoint, which defaults to the
agent-server running the tool (``POST /api/conversations/{id}/children``) and
can be pointed elsewhere via the ``launch_url`` tool parameter so a hosting
backend can run its own provisioning lifecycle.

Usage:
    from openhands.tools.child_conversation import StartChildConversationTool

    agent = Agent(
        llm=llm,
        tools=[Tool(name=StartChildConversationTool.name)],
    )

The tool is registered by the agent-server; it is not part of the default
tool preset because it requires a server to create the child on.
"""

from openhands.tools.child_conversation.definition import (
    StartChildConversationAction,
    StartChildConversationObservation,
    StartChildConversationTool,
)
from openhands.tools.child_conversation.impl import StartChildConversationExecutor


__all__ = [
    "StartChildConversationAction",
    "StartChildConversationExecutor",
    "StartChildConversationObservation",
    "StartChildConversationTool",
]
