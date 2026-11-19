"""A message one conversation sends another, marked with its sender.

Usage: sender.py

Two persisted conversations; the second gets one message from the user and
one sent with `sender=conversation_sender(first.id)`. The program reports the
helper's value and the `sender` of both saved messages, in memory and after a
reload from `persistence_dir`. Scripted: no model call is made.
"""

import uuid

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation
from openhands.sdk.conversation.types import (
    CONVERSATION_SENDER_PREFIX,
    conversation_sender,
)
from openhands.sdk.event import MessageEvent


paths = run_paths()
store = paths["run"] / "sender-store"


def open_conversation(conversation_id=None):
    return Conversation(
        agent=Agent(llm=make_llm(script=[{"finish": "done"}]), tools=[]),
        workspace=str(paths["workspace"]),
        persistence_dir=str(store),
        conversation_id=conversation_id,
        visualizer=None,
    )


def user_messages(conversation) -> list[dict]:
    with conversation.state:
        return [
            {
                "text": e.llm_message.content[0].text,
                "sender": e.sender,
            }
            for e in conversation.state.events
            if isinstance(e, MessageEvent) and e.source == "user"
        ]


sender_conv = open_conversation()
receiver = open_conversation()
receiver_id = receiver.id
value = conversation_sender(sender_conv.id)
receiver.send_message("QA_S04_FROM_USER")
receiver.send_message("QA_S04_FROM_CONVERSATION", sender=value)
in_memory = user_messages(receiver)
receiver.close()
reloaded = open_conversation(receiver_id)
after_reload = user_messages(reloaded)

emit(
    {
        "prefix": CONVERSATION_SENDER_PREFIX,
        "value": value,
        "value_is_prefix_plus_hex": value
        == CONVERSATION_SENDER_PREFIX + sender_conv.id.hex,
        "hex_parses": uuid.UUID(value.removeprefix(CONVERSATION_SENDER_PREFIX))
        == sender_conv.id,
        "in_memory": in_memory,
        "after_reload": after_reload,
    }
)
