"""What a conversation's events say about its turns, without acting on it.

:class:`TurnTracker` reads a conversation's events in order and says, as
:data:`TurnSignal` values, when a turn starts, when the user writes or answers
a card, when a run stops on the user, and how a turn ended. It keeps only the
current turn in memory: no tags, no I/O and no tasks, so each rule can be
tested by feeding it events. The project board
(:mod:`openhands.agent_server.project_board`) acts on the signals.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from openhands.agent_server.project_prompts import AWARENESS_PREFIXES
from openhands.sdk.conversation.impl.local_conversation import (
    ACP_STOP_HOOK_FEEDBACK_PREFIX as STOP_HOOK_FEEDBACK_PREFIX,
)
from openhands.sdk.conversation.resolution import ResolutionState
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import extract_message_text
from openhands.sdk.conversation.types import (
    ASK_MAX_LENGTH,
    CONVERSATION_SENDER_PREFIX,
    GOAL_LOOP_SENDER,
    HELD_FOR_USER_STATUSES,
)
from openhands.sdk.event import (
    ActionEvent,
    ConversationStateUpdateEvent,
    Event,
    MessageEvent,
)
from openhands.sdk.event.ask_user import (
    ANSWER_HEADER_PREFIX,
    AskUserAnswerEvent,
    pending_ask_from_action,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.llm.message import TextContent
from openhands.sdk.tool.builtins.finish import FinishAction


TurnOutcome = Literal["asked", "answered"]

_EXECUTION_STATUS_KEY: Final[str] = "execution_status"
# A question mark that ends a sentence: followed by space, closing markup or
# quotes, or the end of the text. Leaves out "?" inside URLs and code.
_QUESTION_END = re.compile(r"\?(?=[\s*_)\]\"'`»”’]|$)")
_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)


def classify_turn(text: str | None) -> TurnOutcome:
    """Say whether an agent's final message asks the user something.

    Looks at the last paragraph outside code blocks: it asks when a sentence
    there ends with a question mark. This is a heuristic; it errs towards
    ``answered``, which keeps a conversation out of the "needs input" group.
    """
    if not text:
        return "answered"
    prose = _CODE_BLOCK.sub("", text).strip()
    if not prose:
        return "answered"
    last_paragraph = re.split(r"\n\s*\n", prose)[-1]
    return "asked" if _QUESTION_END.search(last_paragraph) else "answered"


_SENTENCE_BREAK = re.compile(r"(?<=[.!?:])\s+|\n")
_MARKUP = re.compile(r"^[\s>*_#`-]+|[*_`]+")


def extract_ask(text: str | None) -> str | None:
    """The question an agent's final message ends on, as one short line.

    Takes the last sentence ending with a question mark in the last paragraph
    outside code blocks (where :func:`classify_turn` looks), drops simple
    Markdown, and cuts it to :data:`ASK_MAX_LENGTH` characters. ``None`` when
    there is no such question.
    """
    if not text:
        return None
    prose = _CODE_BLOCK.sub("", text).strip()
    if not prose:
        return None
    last_paragraph = re.split(r"\n\s*\n", prose)[-1]
    parts = [p for p in _SENTENCE_BREAK.split(last_paragraph) if p.strip()]
    asking = [i for i, part in enumerate(parts) if _QUESTION_END.search(part)]
    if not asking:
        return None
    start = asking[-1]
    # A list item answers to the line that introduces it ("Should I also:").
    if start > 0 and parts[start - 1].rstrip().endswith(":"):
        start -= 1
    ask = " ".join(
        " ".join(_MARKUP.sub("", part).split())
        for part in parts[start : asking[-1] + 1]
    )
    if not ask:
        return None
    if len(ask) > ASK_MAX_LENGTH:
        ask = ask[: ASK_MAX_LENGTH - 1].rstrip() + "…"
    return ask


_REQUEST_MARKERS = re.compile(
    r"\b(let me know|tell me|please|could you|can you|would you|confirm|"
    r"approve|sign[- ]off|review|merge|choose|pick|decide|which (one|of)|"
    r"your call|up to you|waiting (on|for) you)\b",
    re.IGNORECASE,
)


def extract_request(text: str | None) -> str | None:
    """The sentence in a reply that asks something of its reader, as one short
    line: the last one ending with a question mark, else the last one that
    reads as a request ("please review", "let me know", "which one").
    ``None`` when no sentence does: a closing line is never the ask.
    """
    if not text:
        return None
    prose = _CODE_BLOCK.sub("", text).strip()
    sentences = [
        " ".join(_MARKUP.sub("", part).split())
        for part in _SENTENCE_BREAK.split(prose)
        if part.strip()
    ]
    asking = [s for s in sentences if _QUESTION_END.search(s)]
    requesting = [s for s in sentences if _REQUEST_MARKERS.search(s)]
    line = (asking or requesting or [None])[-1]
    if not line:
        return None
    if len(line) > ASK_MAX_LENGTH:
        line = line[: ASK_MAX_LENGTH - 1].rstrip() + "…"
    return line


def from_conversation(sender: str | None) -> bool:
    """Whether ``sender`` names another conversation (``conversation:<id>``)."""
    return sender is not None and sender.startswith(CONVERSATION_SENDER_PREFIX)


def _is_awareness_note(event: MessageEvent) -> bool:
    if event.sender is None or not event.sender.startswith(CONVERSATION_SENDER_PREFIX):
        return False
    content = event.llm_message.content
    first = content[0] if content else None
    return isinstance(first, TextContent) and first.text.startswith(AWARENESS_PREFIXES)


def starts_turn(event: Event) -> bool:
    """A user message that starts a turn: not a ``/goal`` nudge, not a copy
    saved for a coordinator's awareness."""
    return (
        isinstance(event, MessageEvent)
        and event.source == "user"
        and event.sender != GOAL_LOOP_SENDER
        and not _is_awareness_note(event)
    )


def _is_stop_hook_feedback(event: MessageEvent) -> bool:
    if event.source != "environment":
        return False
    text = extract_message_text(event) or ""
    return text.startswith(STOP_HOOK_FEEDBACK_PREFIX)


def _status(event: ConversationStateUpdateEvent) -> str | None:
    key: Any = getattr(event, "key", None)
    value: Any = getattr(event, "value", None)
    if key == _EXECUTION_STATUS_KEY:
        status = value
    elif key == FULL_STATE_KEY and isinstance(value, dict):
        status = value.get(_EXECUTION_STATUS_KEY)
    else:
        return None
    if isinstance(status, ConversationExecutionStatus):
        return status.value
    return status if isinstance(status, str) else None


_ACTION_SUMMARY_MAX = 300


def describe_action(event: ActionEvent) -> str:
    summary = (event.summary or "").strip()
    text = f"{event.tool_name}: {summary}" if summary else event.tool_name
    return text[:_ACTION_SUMMARY_MAX]


def card_text(event: ActionEvent) -> tuple[str, str] | None:
    """The question of an ``ask_user`` call, and the call as text.

    The text is what a coordinator reads of a member's question card: the
    question, then each option with what picking it does. ``None`` when the
    event is not an ``ask_user`` call.
    """
    ask = pending_ask_from_action(event)
    if ask is None or event.action is None:
        return None
    options = event.action.model_dump().get("options") or []
    lines = [f"Question: {ask.question}"]
    for i, label in enumerate(ask.option_labels):
        option = options[i] if i < len(options) else {}
        consequence = option.get("consequence") if isinstance(option, dict) else ""
        mark = " (recommended)" if i == ask.recommended else ""
        lines.append(
            f"{i + 1}. {label}{mark}" + (f": {consequence}" if consequence else "")
        )
    return ask.question, "\n".join(lines)


_TURN_ACTIONS_KEPT: Final[int] = 20


@dataclass(frozen=True)
class TurnStarted:
    """A message started a new turn: the last turn's outcome no longer holds.
    ``by_user`` is false when another conversation (the coordinator) sent it:
    that does not answer a question the user still owes."""

    by_user: bool


@dataclass(frozen=True)
class UserWrote:
    """The user wrote ``text`` in the conversation (not another conversation)."""

    text: str


@dataclass(frozen=True)
class CardAnswered:
    """The user's event may answer a pending ``ask_user`` card: a tap
    (``picked`` is the option's label) or a message of their own."""

    event_id: str
    picked: str | None
    words: str | None


@dataclass(frozen=True)
class TurnStopped:
    """A run stopped on the user: ``status`` is one of
    :data:`~openhands.sdk.conversation.types.HELD_FOR_USER_STATUSES`. ``text``
    says what it waits on: the pending action, the error, or the last message.
    ``sender`` started the turn; when ``sender_known`` is false the tracker
    did not see the turn begin and the event log has to say."""

    status: str
    text: str | None
    sender: str | None
    sender_known: bool


@dataclass(frozen=True)
class TurnEnded:
    """A run the tracker saw finished. ``judged`` is what the resolution judge
    needs, or ``None`` when the turn already says it waits on the user (a
    card, or a question asked earlier in the turn)."""

    turn: TurnOutcome
    ask: str | None
    score: float | None
    text: str | None
    sender: str | None
    sender_known: bool
    judged: ResolutionState | None


TurnSignal = TurnStarted | UserWrote | CardAnswered | TurnStopped | TurnEnded


@dataclass
class TurnTracker:
    """Follow one conversation's current turn from its events.

    :meth:`observe` takes each event in order and returns what it means for
    the turn, as signals. It never acts.
    """

    # The message that started the current turn and what the agent did in
    # it, for the resolution judge.
    _turn_request: str | None = field(default=None, init=False)
    _turn_actions: list[str] = field(default_factory=list, init=False)
    _last_text: str | None = field(default=None, init=False)
    _last_score: float | None = field(default=None, init=False)
    _seen_agent_output: bool = field(default=False, init=False)
    # The agent stopped on a question since the last user message. A Stop
    # hook that refuses to let it stop on a plain message pushes it on to a
    # ``finish`` whose text may not repeat the question; the user still
    # owes an answer.
    _asked_since_user: bool = field(default=False, init=False)
    # The question that made the turn ``asked``, kept for the same reason.
    _ask: str | None = field(default=None, init=False)
    # The question of the ``ask_user`` card the turn ended on: the user owes
    # an answer, so the judge is not asked, also in a turn a coordinator
    # started.
    _card: str | None = field(default=None, init=False)
    # ``sender`` of the latest user message: who started the current turn and
    # so who waits for its final message (None for the user).
    _turn_sender: str | None = field(default=None, init=False)
    # Whether the tracker saw the current turn's first message. After a
    # server restart it did not, and the starter is read from the event log.
    _turn_known: bool = field(default=False, init=False)
    # A run that may stop on the user: set by a user message or by agent
    # output, cleared once a stop or a finish is reported.
    _turn_open: bool = field(default=False, init=False)
    # The held status last reported for this turn, so repeated state pushes
    # of the same stop are told once.
    _reported_hold: str | None = field(default=None, init=False)
    _last_action: str | None = field(default=None, init=False)
    _last_error: str | None = field(default=None, init=False)

    def observe(self, event: Event) -> list[TurnSignal]:
        """What ``event`` means for the current turn."""
        if isinstance(event, AskUserAnswerEvent):
            return [CardAnswered(event.id, event.option_label, event.text)]
        if isinstance(event, MessageEvent):
            return self._message(event)
        if isinstance(event, ActionEvent):
            self._action(event)
            return []
        if isinstance(event, ConversationErrorEvent):
            self._last_error = f"{event.code}: {event.detail}"[:_ACTION_SUMMARY_MAX]
            return []
        if isinstance(event, ConversationStateUpdateEvent):
            return self._state(event)
        return []

    def _message(self, event: MessageEvent) -> list[TurnSignal]:
        if event.source == "user" and not starts_turn(event):
            # A /goal nudge continues the current turn, and a copy saved for
            # a coordinator's awareness neither answers it nor starts one.
            return []
        if event.source == "agent":
            self._note_agent_output(extract_message_text(event), event)
            return []
        if event.source != "user":
            if _is_stop_hook_feedback(event):
                # The agent tried to stop here; a question counts as asked.
                if classify_turn(self._last_text) == "asked":
                    self._asked_since_user = True
                    self._ask = extract_ask(self._last_text) or self._ask
            return []
        self._reset()
        self._asked_since_user = False
        self._ask = None
        self._turn_sender = event.sender
        self._turn_known = True
        self._turn_request = extract_message_text(event)
        self._turn_actions = []
        self._turn_open = True
        self._reported_hold = None
        self._last_action = None
        self._last_error = None
        text = extract_message_text(event) or ""
        # The user's answer passed on by the coordinator answers the user's
        # question; anything else the coordinator says does not.
        by_user = event.sender is None or text.startswith(ANSWER_HEADER_PREFIX)
        signals: list[TurnSignal] = [TurnStarted(by_user=by_user)]
        if event.sender is None:
            if not text.startswith(ANSWER_HEADER_PREFIX):
                # A tap comes with its own answer event; this is the user
                # writing, which also answers a pending card.
                signals.append(CardAnswered(event.id, None, text))
            if text:
                signals.append(UserWrote(text))
        return signals

    def _action(self, event: ActionEvent) -> None:
        card = card_text(event) if event.source == "agent" else None
        if event.source == "agent" and isinstance(event.action, FinishAction):
            self._note_agent_output(event.action.message, event)
        elif card is not None:
            # ask_user ends the turn on a question card.
            question, text = card
            self._note_agent_output(text, event)
            self._card = question
        elif event.source == "agent":
            self._turn_open = True
            self._last_action = describe_action(event)
            self._turn_actions.append(self._last_action)
            del self._turn_actions[:-_TURN_ACTIONS_KEPT]

    def _state(self, event: ConversationStateUpdateEvent) -> list[TurnSignal]:
        status = _status(event)
        if status == ConversationExecutionStatus.RUNNING.value:
            # Resumed: a later stop in the same turn is news again.
            self._reported_hold = None
        elif status in HELD_FOR_USER_STATUSES:
            return self._stopped(status)
        if status != ConversationExecutionStatus.FINISHED.value:
            return []
        # The initial full-state push of an already finished conversation
        # also reads as finished; only a run the tracker saw counts.
        if not self._seen_agent_output:
            return []
        card = self._card
        turn = "asked" if card else classify_turn(self._last_text)
        if card:
            self._ask = card
        elif turn == "asked":
            self._ask = extract_ask(self._last_text) or self._ask
        # Asked in an earlier message of this turn, which the final one does
        # not repeat, or on a card: the judge has nothing to decide.
        carried = card is not None or (self._asked_since_user and turn == "answered")
        if self._asked_since_user:
            turn = "asked"
        self._asked_since_user = turn == "asked"
        ended = TurnEnded(
            turn=turn,
            ask=self._ask if turn == "asked" else None,
            score=self._last_score,
            text=self._last_text,
            sender=self._turn_sender,
            sender_known=self._turn_known,
            judged=None
            if carried
            else ResolutionState(
                request=self._turn_request,
                reply=self._last_text,
                actions=self._turn_actions,
            ),
        )
        self._reset()
        self._turn_open = False
        self._reported_hold = None
        return [ended]

    def _stopped(self, status: str) -> list[TurnSignal]:
        """Report, once, that the turn stopped on the user."""
        if not self._turn_open or self._reported_hold == status:
            return []
        self._reported_hold = status
        if status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION.value:
            text = self._last_action
        elif status == ConversationExecutionStatus.ERROR.value:
            text = self._last_error or self._last_text
        else:
            text = self._last_text
        return [TurnStopped(status, text, self._turn_sender, self._turn_known)]

    def _note_agent_output(
        self, text: str | None, event: MessageEvent | ActionEvent
    ) -> None:
        # Output after a card means its call failed and the turn went on.
        self._card = None
        self._seen_agent_output = True
        self._turn_open = True
        if text:
            self._last_text = text
        if event.critic_result is not None:
            self._last_score = event.critic_result.score

    def _reset(self) -> None:
        self._last_text = None
        self._last_score = None
        self._seen_agent_output = False
        self._card = None
