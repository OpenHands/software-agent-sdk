"""Edits to the agent's earlier messages.

``edit_message`` replaces text in one of the agent's earlier messages, like
``str_replace`` in a file, for example to strike a question that got answered.
It is recorded as a :class:`~openhands.sdk.event.MessageEditEvent`, which
clients show in place of the message's text; the LLM never sees it.

Usage:
    from openhands.tools.message_edit import EditMessageTool

    agent = Agent(llm=llm, tools=[Tool(name=EditMessageTool.name)])
"""

from openhands.tools.message_edit.definition import (
    EditMessageAction,
    EditMessageObservation,
    EditMessageTool,
)


__all__ = [
    "EditMessageAction",
    "EditMessageObservation",
    "EditMessageTool",
]
