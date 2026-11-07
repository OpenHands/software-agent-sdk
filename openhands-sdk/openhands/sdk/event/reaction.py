"""Emoji reactions on messages."""

from collections.abc import Sequence
from typing import Final, Literal, get_args

from pydantic import Field
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import ActionEvent, MessageEvent
from openhands.sdk.event.types import EventID, SourceType


ReactionEmoji = Literal[
    "eyes", "+1", "white_check_mark", "tada", "thinking_face", "heart"
]
"""The emoji a reaction may use, as shortcodes."""

REACTION_EMOJI: Final[tuple[str, ...]] = get_args(ReactionEmoji)

_GLYPHS: Final[dict[str, str]] = {
    "eyes": "\N{EYES}",
    "+1": "\N{THUMBS UP SIGN}",
    "white_check_mark": "\N{WHITE HEAVY CHECK MARK}",
    "tada": "\N{PARTY POPPER}",
    "thinking_face": "\N{THINKING FACE}",
    "heart": "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}",
}


class ReactionEvent(Event):
    """An emoji reaction added to, or removed from, a message.

    The agent reacts with the ``react``/``unreact`` tools, and a person
    through ``POST /api/conversations/{id}/reactions``. Clients fold these
    events into each message's current set of reactions: the latest event for
    a (message, emoji, source) wins. Reactions never reach the LLM.
    """

    source: SourceType = "user"
    target_event_id: EventID = Field(
        description=(
            "The message reacted to: a `MessageEvent`, or the agent's `finish` "
            "`ActionEvent`."
        )
    )
    emoji: ReactionEmoji = Field(description="The emoji, as a shortcode.")
    removed: bool = Field(
        default=False, description="True when this removes the reaction."
    )

    @property
    def glyph(self) -> str:
        return _GLYPHS[self.emoji]

    @property
    def visualize(self) -> Text:
        verb = "removed" if self.removed else "reacted"
        return Text(f"{self.source} {verb} {self.glyph}", style="dim")

    def __str__(self) -> str:
        verb = "removed" if self.removed else "reacted"
        return (
            f"{self.__class__.__name__} ({self.source}): {verb} :{self.emoji}: "
            f"on {self.target_event_id}"
        )


_FINISH_TOOL_NAME: Final[str] = "finish"


def is_reactable(event: Event) -> bool:
    """A message, or the agent's ``finish`` (how an agent usually answers)."""
    if isinstance(event, MessageEvent):
        return True
    return (
        isinstance(event, ActionEvent)
        and event.source == "agent"
        and event.tool_name == _FINISH_TOOL_NAME
    )


def find_reaction_target(
    events: Sequence[Event], target_event_id: EventID | None, *, source: SourceType
) -> Event:
    """The message a reaction from ``source`` is about.

    ``events`` is the conversation's active branch (``state.active_branch()``),
    so a message on a branch the conversation navigated away from is never a
    target. Without ``target_event_id``, the latest message from the other
    side: the latest user message for the agent (the person's, or a
    coordinator's), and for the person the agent's latest message or
    ``finish``.

    Raises:
        ValueError: There is no such message.
    """
    if target_event_id is None:
        wanted = "user" if source == "agent" else "agent"
        for index in range(len(events) - 1, -1, -1):
            event = events[index]
            if is_reactable(event) and event.source == wanted:
                return event
        raise ValueError(f"There is no {wanted} message to react to.")
    event = _find_event(events, target_event_id)
    if event is None:
        raise ValueError(f"No message with id {target_event_id}.")
    if not is_reactable(event):
        raise ValueError(f"Event {target_event_id} is not a message.")
    return event


def _find_event(events: Sequence[Event], event_id: EventID) -> Event | None:
    return next((e for e in reversed(events) if e.id == event_id), None)


def check_reaction_emoji(emoji: str) -> ReactionEmoji:
    """Return ``emoji`` if reactions may use it.

    Raises:
        ValueError: It is not one of :data:`REACTION_EMOJI`; the message lists
            them.
    """
    if emoji not in REACTION_EMOJI:
        allowed = ", ".join(REACTION_EMOJI)
        raise ValueError(f"Unknown emoji {emoji!r}; use one of: {allowed}.")
    return emoji  # type: ignore[return-value]
