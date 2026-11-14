"""The person's answers to the agent's ``ask_user`` questions.

The question itself needs no event of its own: it is the agent's ``ask_user``
call (an :class:`ActionEvent` whose action holds the question and its
options), and the call's result says it was shown. The call ends the agent's
turn, so a question is never waiting inside a run.

A question is *pending* while nothing from the person came after it: no
:class:`AskUserAnswerEvent` (a tap on one of its options) and no message of
their own. Because that is read from the event log, a restart cannot lose a
question or leave it half-answered.

The person answers either way:

- by picking an option (and optionally adding words), which a client sends to
  ``POST /api/conversations/{id}/ask_user/answer``. That records an
  :class:`AskUserAnswerEvent` for the card and a user message, built by
  :func:`answer_message_text`, which starts the agent's next turn;
- by writing in the chat. That is an ordinary message; it closes the question
  as answered in words.

Messages from other conversations or a ``/goal`` loop carry a ``sender`` and
do not close a question: only the person can answer it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from pydantic import Field, model_validator
from rich.text import Text

from openhands.sdk.event.base import Event
from openhands.sdk.event.llm_convertible import (
    ActionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.types import EventID, SourceType


ASK_USER_TOOL_NAME: Final[str] = "ask_user"
"""Name of the tool registered by ``openhands.tools.ask_user``."""

ANSWER_HEADER_PREFIX: Final[str] = "[Answer to your question"
"""Opening of the message an answer becomes, so clients can recognise it."""


class AskUserAnswerEvent(Event):
    """The person picked an option of a pending ``ask_user`` question.

    It is recorded just before the user message that carries the answer to
    the agent, so clients can show the question's card as answered. The LLM
    never sees this event; it reads that message.
    """

    source: SourceType = "user"
    ask_id: EventID = Field(description="The `ask_user` call answered.")
    option_index: int | None = Field(
        default=None, ge=0, description="The option picked, counted from 0."
    )
    option_label: str | None = Field(
        default=None, description="The picked option's label, as it was shown."
    )
    text: str | None = Field(
        default=None, description="The person's own words, when they added any."
    )

    @model_validator(mode="after")
    def _option_or_text(self) -> "AskUserAnswerEvent":
        if self.option_index is None and not (self.text or "").strip():
            raise ValueError("An answer picks an option, gives text, or both.")
        return self

    @property
    def visualize(self) -> Text:
        picked = self.option_label or ""
        words = f" — {self.text}" if self.text else ""
        return Text(f"Answered: {picked}{words}".strip(), style="bold")


@dataclass(frozen=True)
class PendingAsk:
    """A question the person has not answered yet."""

    ask_id: EventID
    question: str
    option_labels: tuple[str, ...]
    recommended: int | None


def is_from_person(event: Event) -> bool:
    """Whether an event is the person's own message or answer.

    Messages relayed from another conversation or sent by a ``/goal`` loop
    carry a ``sender``; the person's own messages have none.
    """
    if isinstance(event, AskUserAnswerEvent):
        return True
    return (
        isinstance(event, MessageEvent)
        and event.source == "user"
        and event.sender is None
    )


def _as_pending(action_event: ActionEvent) -> PendingAsk | None:
    action = action_event.action
    if action is None:
        return None
    question = getattr(action, "question", None)
    options = getattr(action, "options", None) or ()
    if not isinstance(question, str):
        return None
    labels = tuple(str(getattr(o, "label", o)) for o in options)
    recommended = getattr(action, "recommended", None)
    return PendingAsk(
        ask_id=action_event.id,
        question=question,
        option_labels=labels,
        recommended=recommended if isinstance(recommended, int) else None,
    )


def find_pending_ask(events: Sequence[Event]) -> PendingAsk | None:
    """The agent's latest ``ask_user`` question, unless the person answered it.

    Args:
        events: The conversation's events, oldest first (its active branch).
    """
    asked_action_id: EventID | None = None
    for event in reversed(events):
        if is_from_person(event):
            return None
        if (
            isinstance(event, ObservationEvent)
            and event.tool_name == ASK_USER_TOOL_NAME
            and not event.observation.is_error
        ):
            asked_action_id = event.action_id
            break
    if asked_action_id is None:
        return None
    for event in reversed(events):
        if isinstance(event, ActionEvent) and event.id == asked_action_id:
            return _as_pending(event)
    return None


def answer_message_text(
    pending: PendingAsk, option_index: int | None, text: str | None
) -> str:
    """The message the agent reads for an answer picked on the card."""
    lines = [f'{ANSWER_HEADER_PREFIX}: "{pending.question}"]']
    if option_index is not None:
        lines.append(pending.option_labels[option_index])
    if text and text.strip():
        lines.append(text.strip())
    return "\n".join(lines)
