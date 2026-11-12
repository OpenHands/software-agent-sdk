"""Record how a project conversation's turn ended, for the project board.

When a conversation in a project finishes a run, :class:`ConversationOutcomeSubscriber`
tags it with how the agent's last message ended (``turn=asked`` or
``turn=answered``) and, when a turn leaves nothing for the user to do, marks
it resolved (``resolved=auto``). Two signals can resolve a turn: the resolution
judge (:mod:`openhands.sdk.conversation.resolution`), which asks whether
anything is left for the user, and a confident critic score. A turn that
asked the user something is never resolved automatically. When the agent
stopped on a question, the question itself is saved, in one short line, as
``ask``. A new user message
removes these tags, since the conversation is open again. Once the agent stops
on a question, the turn (and its ``ask``) stays until the user answers, even
if a Stop hook made it go on to a ``finish`` that does not repeat the question.

The board reads these tags from the conversation catalog, so listing a project
never loads conversation state or events.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from openhands.agent_server.project_prompts import AWARENESS_PREFIXES
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.conversation.impl.local_conversation import (
    ACP_STOP_HOOK_FEEDBACK_PREFIX as STOP_HOOK_FEEDBACK_PREFIX,
)
from openhands.sdk.conversation.resolution import ResolutionState, ResolutionVerdict
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import extract_message_text
from openhands.sdk.conversation.types import (
    ASK_MAX_LENGTH,
    ASK_TAG_KEY,
    BOARD_TAG_KEYS,
    CONVERSATION_SENDER_PREFIX,
    GOAL_LOOP_SENDER,
    HELD_FOR_PERSON_STATUSES,
    PROJECT_TAG_KEY,
    RESOLVE_NOTE_TAG_KEY,
    RESOLVED_TAG_KEY,
    TURN_TAG_KEY,
)
from openhands.sdk.event import (
    ActionEvent,
    ConversationStateUpdateEvent,
    Event,
    MessageEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.llm.message import TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.builtins.finish import FinishAction


logger = get_logger(__name__)

AUTO_RESOLVE_THRESHOLD: Final[float] = 0.9
"""Critic score at or above which a finished conversation is marked resolved."""

TurnOutcome = Literal["asked", "answered"]
ResolvedBy = Literal["user", "auto"]

AGENT_RESOLVE_NOTE = "agent"
"""``resolvenote`` of a conversation its own agent resolved, with
``resolve_conversation``: nothing left, by the agent's own account. The judge
does not run on such a turn."""

_EXECUTION_STATUS_KEY: Final[str] = "execution_status"
# A question mark that ends a sentence: followed by space, closing markup or
# quotes, or the end of the text. Leaves out "?" inside URLs and code.
_QUESTION_END = re.compile(r"\?(?=[\s*_)\]\"'`»”’]|$)")
_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)


SetTag = Callable[..., Awaitable[Any]]
"""``ConversationService.set_conversation_tag``-shaped callable."""

OnFinished = Callable[[str, TurnOutcome, str | None, str | None], Awaitable[object]]
"""Called after a project member's outcome is recorded, with the project id
(hex), the turn outcome, the agent's final message and the ``sender`` of the
user message that started the turn (None when the user sent it)."""


OnUserMessage = Callable[[str, str], Awaitable[object]]

OnStopped = Callable[[str, str, str | None, str | None], Awaitable[object]]
"""``(project, status, text, turn_sender)`` when a run stops on the user:
waiting for confirmation, paused, error or stuck (see
:data:`~openhands.sdk.conversation.types.HELD_FOR_PERSON_STATUSES`). ``text``
says what it waits on: the pending action, the error, or the last message."""

ResolutionJudge = Callable[[ResolutionState], Awaitable[ResolutionVerdict | None]]
"""Judges whether an answered turn leaves anything for the user; ``None``
when no judge model is available or the call failed."""

_TURN_ACTIONS_KEPT: Final[int] = 20

TurnSenderLookup = Callable[[], Awaitable[str | None]]

ProjectsEnabled = Callable[[], Awaitable[bool]]
"""Whether the user has the ``projects`` experimental feature on right now.

The subscriber is Projects' one hook into a conversation's events, so this is
its one check: while it is off the board is left as it is (no outcome, judge or
reopen) and the coordinator hears nothing."""
"""Reads who started the current turn from the event log, for a subscriber
that did not see the turn's first message (a server restart in between)."""
"""Called when the user writes in a project member, with the project id
(hex) and the message text."""


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


def _from_conversation(sender: str | None) -> bool:
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


def _finished(event: ConversationStateUpdateEvent) -> bool:
    return _status(event) == ConversationExecutionStatus.FINISHED.value


_ACTION_SUMMARY_MAX = 300


def describe_action(event: ActionEvent) -> str:
    summary = (event.summary or "").strip()
    text = f"{event.tool_name}: {summary}" if summary else event.tool_name
    return text[:_ACTION_SUMMARY_MAX]


@dataclass
class ConversationOutcomeSubscriber(Subscriber[Event]):
    """Tag a project conversation with how its runs end.

    ``tags`` returns the conversation's current tags; only conversations with a
    ``project`` tag are changed, so a conversation that joins a project later
    is covered from its next run on. Tag writes run in background tasks, one
    after another in event order, so publishing is never slowed down.
    """

    conversation_id: UUID
    tags: Callable[[], dict[str, str]]
    set_tag: SetTag
    on_finished: OnFinished | None = None
    on_user_message: OnUserMessage | None = None
    on_stopped: OnStopped | None = None
    turn_sender_lookup: TurnSenderLookup | None = None
    judge: ResolutionJudge | None = None
    projects_enabled: ProjectsEnabled | None = None
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
    # ``sender`` of the latest user message: who started the current turn and
    # so who waits for its final message (None for the user).
    _turn_sender: str | None = field(default=None, init=False)
    # Whether this subscriber saw the current turn's first message. After a
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
    _tail: asyncio.Task | None = field(default=None, init=False)
    # Messages to the coordinator, delivered one after another in event order
    # and apart from the tag writes.
    _notify_tail: asyncio.Task | None = field(default=None, init=False)

    async def __call__(self, event: Event) -> None:
        try:
            self._handle(event)
        except Exception:
            logger.warning(
                "Project board update failed for conversation %s",
                self.conversation_id,
                exc_info=True,
            )

    def _handle(self, event: Event) -> None:
        if isinstance(event, MessageEvent):
            if event.source == "user" and not starts_turn(event):
                # A /goal nudge continues the current turn, and a copy saved
                # for a coordinator's awareness neither answers it nor starts
                # one.
                return
            if event.source == "user":
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
                if event.sender is None and self.on_user_message is not None:
                    project = self.tags().get(PROJECT_TAG_KEY)
                    text = extract_message_text(event)
                    if project and text:
                        callback = self.on_user_message
                        self._notify_later(lambda: callback(project, text))
                # Reopen a member the user resolved (checked when it runs).
                # An outcome still being written is not visible in the tags
                # yet, so queue the reopen behind it as well.
                pending = self._tail is not None and not self._tail.done()
                if pending or any(key in self.tags() for key in BOARD_TAG_KEYS):
                    self._enqueue(self._reopen)
            elif event.source == "agent":
                self._note_agent_output(extract_message_text(event), event)
            elif _is_stop_hook_feedback(event):
                # The agent tried to stop here; a question counts as asked.
                if classify_turn(self._last_text) == "asked":
                    self._asked_since_user = True
                    self._ask = extract_ask(self._last_text) or self._ask
            return
        if isinstance(event, ActionEvent):
            if event.source == "agent" and isinstance(event.action, FinishAction):
                self._note_agent_output(event.action.message, event)
            elif event.source == "agent":
                self._turn_open = True
                self._last_action = describe_action(event)
                self._turn_actions.append(self._last_action)
                del self._turn_actions[:-_TURN_ACTIONS_KEPT]
            return
        if isinstance(event, ConversationErrorEvent):
            self._last_error = f"{event.code}: {event.detail}"[:_ACTION_SUMMARY_MAX]
            return
        if isinstance(event, ConversationStateUpdateEvent):
            status = _status(event)
            if status == ConversationExecutionStatus.RUNNING.value:
                # Resumed: a later stop in the same turn is news again.
                self._reported_hold = None
            elif status in HELD_FOR_PERSON_STATUSES:
                self._stopped(status)
                return
        if isinstance(event, ConversationStateUpdateEvent) and _finished(event):
            # The initial full-state push of an already finished conversation
            # also reads as finished; only a run this subscriber saw counts.
            if not self._seen_agent_output:
                return
            turn = classify_turn(self._last_text)
            if turn == "asked":
                self._ask = extract_ask(self._last_text) or self._ask
            # Asked in an earlier message of this turn, which the final one
            # does not repeat: the judge sees only the final one.
            carried = self._asked_since_user and turn == "answered"
            if self._asked_since_user:
                turn = "asked"
            self._asked_since_user = turn == "asked"
            ask = self._ask if turn == "asked" else None
            score = self._last_score
            text = self._last_text
            sender = self._turn_sender
            known = self._turn_known
            self._reset()
            self._turn_open = False
            self._reported_hold = None
            judged = ResolutionState(
                request=self._turn_request, reply=text, actions=self._turn_actions
            )
            project = self.tags().get(PROJECT_TAG_KEY)
            if project:
                self._enqueue(
                    lambda: self._record(
                        project,
                        turn,
                        score,
                        text,
                        sender,
                        ask,
                        known,
                        None if carried else judged,
                    )
                )

    def _stopped(self, status: str) -> None:
        """Tell the coordinator, once, that a turn stopped on the user."""
        if not self._turn_open or self._reported_hold == status:
            return
        self._reported_hold = status
        project = self.tags().get(PROJECT_TAG_KEY)
        if not project or self.on_stopped is None:
            return
        if status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION.value:
            text = self._last_action
        elif status == ConversationExecutionStatus.ERROR.value:
            text = self._last_error or self._last_text
        else:
            text = self._last_text
        sender = self._turn_sender
        known = self._turn_known
        callback = self.on_stopped

        async def notify() -> None:
            starter = sender if known else await self._logged_turn_sender()
            await callback(project, status, text, starter)

        self._notify_later(notify)

    async def _logged_turn_sender(self) -> str | None:
        if self.turn_sender_lookup is None:
            return None
        return await self.turn_sender_lookup()

    def _note_agent_output(
        self, text: str | None, event: MessageEvent | ActionEvent
    ) -> None:
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

    def _enqueue(self, step: Callable[[], Awaitable[None]]) -> None:
        previous = self._tail

        async def run() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            try:
                await step()
            except Exception:
                logger.warning(
                    "Project board tag update failed for conversation %s",
                    self.conversation_id,
                    exc_info=True,
                )

        self._tail = asyncio.create_task(run())

    async def _enabled(self) -> bool:
        return self.projects_enabled is None or await self.projects_enabled()

    async def _reopen(self) -> None:
        # Only a project member's board state is the server's to clear.
        if PROJECT_TAG_KEY not in self.tags() or not await self._enabled():
            return
        # Most user messages find neither tag; each write is a metadata save.
        for key in sorted(BOARD_TAG_KEYS):
            if key in self.tags():
                await self.set_tag(self.conversation_id, key, None)

    async def _record(
        self,
        project: str,
        turn: TurnOutcome,
        score: float | None,
        text: str | None,
        sender: str | None,
        ask: str | None = None,
        sender_known: bool = True,
        judged: ResolutionState | None = None,
    ) -> None:
        if not await self._enabled():
            return
        verdict = None
        shown = False
        if judged is not None and RESOLVED_TAG_KEY not in self.tags():
            # The judge can take a while; the board shows the heuristic's
            # outcome meanwhile rather than no outcome at all.
            await self._set_turn(turn, ask)
            shown = True
            verdict = await self._judge(judged)
        heuristic = (turn, ask)
        if verdict is not None:
            # The judge decides whether the reply waits on the user, also
            # when it asks without a question mark or ends on a pleasantry.
            starter = sender if sender_known else await self._logged_turn_sender()
            if verdict.needs_user and _from_conversation(starter):
                # Asks in a turn another conversation started (a coordinator)
                # are for that conversation, which gets the reply; the turn
                # is neither the user's to answer nor resolved.
                turn = "answered"
                ask = None
            elif verdict.needs_user:
                turn = "asked"
                ask = ask or extract_request(text)
            else:
                turn = "answered"
                ask = None
        if not shown or (turn, ask) != heuristic:
            await self._set_turn(turn, ask)
        self._notify_finished(project, turn, text, sender, sender_known)
        # A turn that asked the user something is theirs to answer, and a
        # conversation already resolved (by the user or before) stays so.
        if turn == "asked" or RESOLVED_TAG_KEY in self.tags():
            return
        if verdict is not None and verdict.needs_user:
            return
        note: str | None = None
        if verdict is not None and verdict.resolved:
            note = verdict.note()
        elif verdict is None and score is not None and score >= AUTO_RESOLVE_THRESHOLD:
            # Without a judge, a confident critic still resolves.
            note = f"{score:.2f} critic"
        if verdict is not None and note is None:
            logger.debug(
                "Conversation %s not resolved: %s", self.conversation_id, verdict.note()
            )
        if note is None:
            return
        # Never overwrite the user's own resolve.
        await self.set_tag(
            self.conversation_id, RESOLVED_TAG_KEY, "auto", only_if_absent=True
        )
        if self.tags().get(RESOLVED_TAG_KEY) == "auto":
            await self.set_tag(self.conversation_id, RESOLVE_NOTE_TAG_KEY, note)

    async def _set_turn(self, turn: TurnOutcome, ask: str | None) -> None:
        await self.set_tag(self.conversation_id, TURN_TAG_KEY, turn)
        if ask is not None or ASK_TAG_KEY in self.tags():
            await self.set_tag(self.conversation_id, ASK_TAG_KEY, ask)

    async def _judge(self, state: ResolutionState) -> ResolutionVerdict | None:
        if self.judge is None:
            return None
        try:
            return await self.judge(state)
        except Exception as e:
            # The error goes in the message too: some log handlers drop
            # tracebacks.
            logger.warning(
                "Resolution judge failed for conversation %s: %s: %s",
                self.conversation_id,
                type(e).__name__,
                e,
                exc_info=True,
            )
            return None

    def _notify_finished(
        self,
        project: str,
        turn: TurnOutcome,
        text: str | None,
        sender: str | None,
        sender_known: bool,
    ) -> None:
        if self.on_finished is not None:
            # Delivering to the coordinator can wait for its whole agent step;
            # keep it off the tag-write chain.
            callback = self.on_finished

            async def notify() -> None:
                starter = sender if sender_known else await self._logged_turn_sender()
                await callback(project, turn, text, starter)

            self._notify_later(notify)

    def _notify_later(self, step: Callable[[], Awaitable[object]]) -> None:
        previous = self._notify_tail

        async def run() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            try:
                if await self._enabled():
                    await step()
            except Exception:
                logger.warning(
                    "Notifying the coordinator about conversation %s failed",
                    self.conversation_id,
                    exc_info=True,
                )

        self._notify_tail = asyncio.create_task(run())

    async def drain(self) -> None:
        """Wait for queued tag updates and notifications (tests, shutdown)."""
        if self._tail is not None:
            await asyncio.gather(self._tail, return_exceptions=True)
        if self._notify_tail is not None:
            await asyncio.gather(self._notify_tail, return_exceptions=True)
