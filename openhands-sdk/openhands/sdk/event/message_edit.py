"""Edits to the agent's earlier messages.

The event log is append-only, so an edit never changes a message: it is a new
event that names the message and carries its full new text. Clients show the
latest edit in place of the message's text, marked as edited.
"""

from collections.abc import Sequence

from pydantic import Field, field_validator
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent
from openhands.sdk.event.reaction import is_reactable
from openhands.sdk.event.types import EventID, SourceType
from openhands.sdk.llm import TextContent


class MessageEditEvent(Event):
    """The agent's edit of one of its earlier messages.

    The agent edits with the ``edit_message`` tool, for example to strike a
    question that got answered or a claim that became wrong. ``text`` is the
    whole message after the edit; the latest edit of a message wins.

    The LLM never sees this event: its ``edit_message`` call and result are
    already in its history, and the message itself stays there as first
    written.
    """

    source: SourceType = "agent"
    target_event_id: EventID = Field(
        description=(
            "The edited message: an agent `MessageEvent`, or the agent's "
            "`finish` `ActionEvent`."
        )
    )
    text: str = Field(description="The message's whole text after the edit.")

    @field_validator("source")
    @classmethod
    def _agent(cls, source: SourceType) -> SourceType:
        if source != "agent":
            raise ValueError("Only the agent edits its messages.")
        return source

    @property
    def visualize(self) -> Text:
        return Text(f"agent edited a message:\n{self.text}", style="dim")

    def __str__(self) -> str:
        return f"{self.__class__.__name__} ({self.source}): on {self.target_event_id}"


def is_editable(event: Event) -> bool:
    """An agent message, or the agent's ``finish``."""
    return is_reactable(event) and event.source == "agent"


def message_text(events: Sequence[Event], message: Event) -> str:
    """A message's text as the user sees it: after its latest edit, if any.

    ``events`` is the conversation's active branch.
    """
    for event in reversed(events):
        if isinstance(event, MessageEditEvent) and event.target_event_id == message.id:
            return event.text
    return _written_text(message)


def _written_text(message: Event) -> str:
    from openhands.sdk.tool.builtins.finish import FinishAction

    if isinstance(message, MessageEvent):
        return "\n".join(
            c.text for c in message.llm_message.content if isinstance(c, TextContent)
        )
    if isinstance(message, ActionEvent) and isinstance(message.action, FinishAction):
        return message.action.message
    return ""


def find_edit(events: Sequence[Event], old_str: str, new_str: str) -> MessageEditEvent:
    """The edit that replaces ``old_str`` with ``new_str`` in an agent message.

    ``old_str`` must occur exactly once across the agent's messages on the
    active branch (``events``), each taken as it reads after its latest edit,
    like ``str_replace`` in a file.

    Raises:
        ValueError: ``old_str`` is empty, unchanged, not found, or found more
            than once.
    """
    if not old_str:
        raise ValueError(
            "old_str is empty; copy the text to replace from your message."
        )
    if old_str == new_str:
        raise ValueError("new_str is the same as old_str; nothing to edit.")
    matches: list[tuple[Event, str]] = []
    count = 0
    for event in events:
        if not is_editable(event):
            continue
        text = message_text(events, event)
        occurrences = text.count(old_str)
        if occurrences:
            matches.append((event, text))
            count += occurrences
    if count == 0:
        raise ValueError(
            "old_str is not in any of your messages. Copy it exactly from the "
            "message as it reads now, including any earlier edit."
        )
    if count > 1:
        raise ValueError(
            f"old_str occurs {count} times in your messages; include more of "
            "the text around it so it is unique."
        )
    target, text = matches[0]
    return MessageEditEvent(
        target_event_id=target.id, text=text.replace(old_str, new_str, 1)
    )
