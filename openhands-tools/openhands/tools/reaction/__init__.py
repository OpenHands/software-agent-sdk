"""Emoji reactions for agents.

``react`` puts an emoji on a message (by default the person's latest one) and
``unreact`` takes it off. A reaction is a small visible sign that the agent saw
a message, for when the work comes before the answer. It is recorded as a
:class:`~openhands.sdk.event.ReactionEvent`, which clients show on the message
and the LLM never sees.

Usage:
    from openhands.tools.reaction import ReactTool, UnreactTool

    agent = Agent(
        llm=llm,
        tools=[Tool(name=ReactTool.name), Tool(name=UnreactTool.name)],
    )
"""

from openhands.tools.reaction.definition import (
    ReactAction,
    ReactionObservation,
    ReactTool,
    UnreactAction,
    UnreactTool,
)


__all__ = [
    "ReactAction",
    "ReactTool",
    "ReactionObservation",
    "UnreactAction",
    "UnreactTool",
]
