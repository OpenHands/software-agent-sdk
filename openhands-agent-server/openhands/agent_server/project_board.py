"""Record how a project conversation's turn ended, for the project board.

When a conversation in a project finishes a run, :class:`ConversationOutcomeSubscriber`
tags it with how the agent's last message ended (``turn=asked`` or
``turn=answered``) and, when the critic is confident the task succeeded, marks
it resolved (``resolved=auto``). When the agent stopped on a question, the
question itself is saved, in one short line, as ``ask``. A new user message
removes these tags, since the conversation is open again. Once the agent stops
on a question, the turn (and its ``ask``) stays until the person answers, even
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
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import extract_message_text
from openhands.sdk.conversation.types import (
    ASK_MAX_LENGTH,
    ASK_TAG_KEY,
    BOARD_TAG_KEYS,
    CONVERSATION_SENDER_PREFIX,
    PROJECT_TAG_KEY,
    RESOLVED_TAG_KEY,
    TURN_TAG_KEY,
)
from openhands.sdk.event import (
    ActionEvent,
    ConversationStateUpdateEvent,
    Event,
    MessageEvent,
)
from openhands.sdk.event.conversation_state import FULL_STATE_KEY
from openhands.sdk.llm.message import TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.builtins.finish import FinishAction


logger = get_logger(__name__)

AUTO_RESOLVE_THRESHOLD: Final[float] = 0.9
"""Critic score at or above which a finished conversation is marked resolved."""

TurnOutcome = Literal["asked", "answered"]
ResolvedBy = Literal["user", "auto"]

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
user message that started the turn (None when the person sent it)."""


OnPersonMessage = Callable[[str, str], Awaitable[object]]
"""Called when the person writes in a project member, with the project id
(hex) and the message text."""


def classify_turn(text: str | None) -> TurnOutcome:
    """Say whether an agent's final message asks the person something.

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


def _is_awareness_note(event: MessageEvent) -> bool:
    if event.sender is None or not event.sender.startswith(CONVERSATION_SENDER_PREFIX):
        return False
    content = event.llm_message.content
    first = content[0] if content else None
    return isinstance(first, TextContent) and first.text.startswith(AWARENESS_PREFIXES)


def _is_stop_hook_feedback(event: MessageEvent) -> bool:
    if event.source != "environment":
        return False
    text = extract_message_text(event) or ""
    return text.startswith(STOP_HOOK_FEEDBACK_PREFIX)


def _finished(event: ConversationStateUpdateEvent) -> bool:
    key: Any = getattr(event, "key", None)
    value: Any = getattr(event, "value", None)
    if key == _EXECUTION_STATUS_KEY:
        status = value
    elif key == FULL_STATE_KEY and isinstance(value, dict):
        status = value.get(_EXECUTION_STATUS_KEY)
    else:
        return False
    return status == ConversationExecutionStatus.FINISHED.value


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
    on_person_message: OnPersonMessage | None = None
    _last_text: str | None = field(default=None, init=False)
    _last_score: float | None = field(default=None, init=False)
    _seen_agent_output: bool = field(default=False, init=False)
    # The agent stopped on a question since the last user message. A Stop
    # hook that refuses to let it stop on a plain message pushes it on to a
    # ``finish`` whose text may not repeat the question; the person still
    # owes an answer.
    _asked_since_user: bool = field(default=False, init=False)
    # The question that made the turn ``asked``, kept for the same reason.
    _ask: str | None = field(default=None, init=False)
    # ``sender`` of the latest user message: who started the current turn and
    # so who waits for its final message (None for the person).
    _turn_sender: str | None = field(default=None, init=False)
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
            if event.source == "user" and _is_awareness_note(event):
                # A copy for the coordinator's awareness, saved without a run:
                # it neither answers the coordinator nor starts a turn.
                return
            if event.source == "user":
                self._reset()
                self._asked_since_user = False
                self._ask = None
                self._turn_sender = event.sender
                if event.sender is None and self.on_person_message is not None:
                    project = self.tags().get(PROJECT_TAG_KEY)
                    text = extract_message_text(event)
                    if project and text:
                        callback = self.on_person_message
                        self._notify_later(lambda: callback(project, text))
                # Reopen any conversation the person resolved, in a project
                # or not; recording outcomes is limited to project members.
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
            return
        if isinstance(event, ConversationStateUpdateEvent) and _finished(event):
            # The initial full-state push of an already finished conversation
            # also reads as finished; only a run this subscriber saw counts.
            if not self._seen_agent_output:
                return
            turn = classify_turn(self._last_text)
            if turn == "asked":
                self._ask = extract_ask(self._last_text) or self._ask
            if self._asked_since_user:
                turn = "asked"
            self._asked_since_user = turn == "asked"
            ask = self._ask if turn == "asked" else None
            score = self._last_score
            text = self._last_text
            sender = self._turn_sender
            self._reset()
            project = self.tags().get(PROJECT_TAG_KEY)
            if project:
                self._enqueue(
                    lambda: self._record(project, turn, score, text, sender, ask)
                )

    def _note_agent_output(
        self, text: str | None, event: MessageEvent | ActionEvent
    ) -> None:
        self._seen_agent_output = True
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

    async def _reopen(self) -> None:
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
    ) -> None:
        await self.set_tag(self.conversation_id, TURN_TAG_KEY, turn)
        if ask is not None or ASK_TAG_KEY in self.tags():
            await self.set_tag(self.conversation_id, ASK_TAG_KEY, ask)
        if score is not None and score >= AUTO_RESOLVE_THRESHOLD:
            # Never overwrite the person's own resolve.
            await self.set_tag(
                self.conversation_id,
                RESOLVED_TAG_KEY,
                "auto",
                only_if_absent=True,
            )
        if self.on_finished is not None:
            # Delivering to the coordinator can wait for its whole agent step;
            # keep it off the tag-write chain.
            callback = self.on_finished
            self._notify_later(lambda: callback(project, turn, text, sender))

    def _notify_later(self, step: Callable[[], Awaitable[object]]) -> None:
        previous = self._notify_tail

        async def run() -> None:
            if previous is not None:
                await asyncio.gather(previous, return_exceptions=True)
            try:
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
