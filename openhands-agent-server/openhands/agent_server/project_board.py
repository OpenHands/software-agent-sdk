"""Record how a project conversation's turn ended, for the project board.

:class:`ConversationOutcomeSubscriber` acts on what a
:class:`~openhands.agent_server.project_turns.TurnTracker` reads from a
project member's events. When a run finishes it tags the conversation with
how the agent's last message ended (``turn=asked`` or ``turn=answered``) and,
when a turn leaves nothing for the user to do, marks it resolved
(``resolved=auto``). Two signals can resolve a turn: the resolution judge
(:mod:`openhands.sdk.conversation.resolution`), which asks whether anything is
left for the user, and a confident critic score. A turn that asked the user
something is never resolved automatically. When the agent stopped on a
question, the question itself is saved, in one short line, as ``ask``. A new
user message removes these tags, since the conversation is open again. It also
tells the coordinator what it needs to hear: finished turns, stops on the
user, the user's own messages and answers to cards.

The board reads these tags from the conversation catalog, so listing a project
never loads conversation state or events.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from openhands.agent_server.project_turns import (
    CardAnswered,
    TurnEnded,
    TurnOutcome,
    TurnSignal,
    TurnStarted,
    TurnStopped,
    TurnTracker,
    UserWrote,
    extract_request,
    from_conversation,
)
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.conversation.resolution import ResolutionState, ResolutionVerdict
from openhands.sdk.conversation.types import (
    ASK_TAG_KEY,
    BOARD_TAG_KEYS,
    PROJECT_TAG_KEY,
    RESOLVE_NOTE_TAG_KEY,
    RESOLVED_TAG_KEY,
    TURN_TAG_KEY,
)
from openhands.sdk.event import Event
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

AUTO_RESOLVE_THRESHOLD: Final[float] = 0.9
"""Critic score at or above which a finished conversation is marked resolved."""

ResolvedBy = Literal["user", "auto"]

AGENT_RESOLVE_NOTE = "agent"
"""``resolvenote`` of a conversation its own agent resolved, with
``resolve_conversation``: nothing left, by the agent's own account. The judge
does not run on such a turn."""


SetTag = Callable[..., Awaitable[Any]]
"""``ConversationService.set_conversation_tag``-shaped callable."""

OnFinished = Callable[[str, TurnOutcome, str | None, str | None], Awaitable[object]]
"""Called after a project member's outcome is recorded, with the project id
(hex), the turn outcome, the agent's final message and the ``sender`` of the
user message that started the turn (None when the user sent it)."""


OnUserMessage = Callable[[str, str], Awaitable[object]]
"""Called when the user writes in a project member, with the project id
(hex) and the message text."""

OnUserAnswer = Callable[[str, str, str | None, str | None], Awaitable[object]]
"""``(project, event_id, picked, words)`` when the user's event may answer an
``ask_user`` card: a tap on one (``picked`` is the option's label) or a
message of their own. Lets a coordinator's card pass the answer on."""

OnStopped = Callable[[str, str, str | None, str | None], Awaitable[object]]
"""``(project, status, text, turn_sender)`` when a run stops on the user:
waiting for confirmation, paused, error or stuck (see
:data:`~openhands.sdk.conversation.types.HELD_FOR_USER_STATUSES`). ``text``
says what it waits on: the pending action, the error, or the last message."""

ResolutionJudge = Callable[[ResolutionState], Awaitable[ResolutionVerdict | None]]
"""Judges whether an answered turn leaves anything for the user; ``None``
when no judge model is available or the call failed."""

TurnSenderLookup = Callable[[], Awaitable[str | None]]
"""Reads who started the current turn from the event log, for a subscriber
that did not see the turn's first message (a server restart in between)."""

ProjectsEnabled = Callable[[], Awaitable[bool]]
"""Whether the user has the ``projects`` experimental feature on right now.

The subscriber is Projects' one hook into a conversation's events, so this is
its one check: while it is off the board is left as it is (no outcome, judge or
reopen) and the coordinator hears nothing."""


@dataclass
class ConversationOutcomeSubscriber(Subscriber[Event]):
    """Tag a project conversation with how its runs end, and tell its
    coordinator.

    ``tags`` returns the conversation's current tags; only conversations with a
    ``project`` tag are changed. Tag writes run in background tasks, one after
    another in event order, so publishing is never slowed down; messages to the
    coordinator run on a second such chain.
    """

    conversation_id: UUID
    tags: Callable[[], dict[str, str]]
    set_tag: SetTag
    on_finished: OnFinished | None = None
    on_user_message: OnUserMessage | None = None
    on_stopped: OnStopped | None = None
    on_user_answer: OnUserAnswer | None = None
    turn_sender_lookup: TurnSenderLookup | None = None
    judge: ResolutionJudge | None = None
    projects_enabled: ProjectsEnabled | None = None
    tracker: TurnTracker = field(default_factory=TurnTracker)
    _tail: asyncio.Task | None = field(default=None, init=False)
    _notify_tail: asyncio.Task | None = field(default=None, init=False)
    _judging: "asyncio.Future[ResolutionVerdict | None] | None" = field(
        default=None, init=False
    )

    async def __call__(self, event: Event) -> None:
        try:
            for signal in self.tracker.observe(event):
                self._act(signal)
        except Exception:
            logger.warning(
                "Project board update failed for conversation %s",
                self.conversation_id,
                exc_info=True,
            )

    def _act(self, signal: TurnSignal) -> None:
        if isinstance(signal, TurnStarted):
            self._turn_started()
            return
        project = self.tags().get(PROJECT_TAG_KEY)
        if not project:
            return
        if isinstance(signal, CardAnswered):
            if self.on_user_answer is not None:
                answer = self.on_user_answer
                self._notify_later(
                    lambda: answer(
                        project, signal.event_id, signal.picked, signal.words
                    )
                )
        elif isinstance(signal, UserWrote):
            if self.on_user_message is not None:
                relay = self.on_user_message
                self._notify_later(lambda: relay(project, signal.text))
        elif isinstance(signal, TurnStopped):
            if self.on_stopped is not None:
                self._notify_stopped(project, signal, self.on_stopped)
        else:
            self._enqueue(lambda: self._record(project, signal))

    def _turn_started(self) -> None:
        # The user moved on: the last turn's verdict no longer matters, and
        # the reopen below should not wait for it.
        if self._judging is not None:
            self._judging.cancel()
        # Reopen a member the user resolved (checked when it runs). An
        # outcome still being written is not visible in the tags yet, so
        # queue the reopen behind it as well.
        pending = self._tail is not None and not self._tail.done()
        if pending or any(key in self.tags() for key in BOARD_TAG_KEYS):
            self._enqueue(self._reopen)

    def _notify_stopped(
        self, project: str, signal: TurnStopped, callback: OnStopped
    ) -> None:
        async def notify() -> None:
            starter = await self._starter(signal.sender, signal.sender_known)
            await callback(project, signal.status, signal.text, starter)

        self._notify_later(notify)

    async def _starter(self, sender: str | None, known: bool) -> str | None:
        if known:
            return sender
        if self.turn_sender_lookup is None:
            return None
        return await self.turn_sender_lookup()

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

    async def _record(self, project: str, ended: TurnEnded) -> None:
        if not await self._enabled():
            return
        turn, ask, text = ended.turn, ended.ask, ended.text
        verdict = None
        shown = False
        if ended.judged is not None and RESOLVED_TAG_KEY not in self.tags():
            # The judge can take a while; the board shows the heuristic's
            # outcome meanwhile rather than no outcome at all.
            await self._set_turn(turn, ask)
            shown = True
            judging = asyncio.ensure_future(self._judge(ended.judged))
            self._judging = judging
            await asyncio.wait({judging})
            self._judging = None
            # Cancelled by a new user message: keep the heuristic's outcome.
            verdict = None if judging.cancelled() else judging.result()
        heuristic = (turn, ask)
        if verdict is not None:
            # The judge decides whether the reply waits on the user, also
            # when it asks without a question mark or ends on a pleasantry.
            starter = await self._starter(ended.sender, ended.sender_known)
            if verdict.needs_user and from_conversation(starter):
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
        self._notify_finished(project, turn, ended)
        # A turn that asked the user something is theirs to answer, and a
        # conversation already resolved (by the user or before) stays so.
        if turn == "asked" or RESOLVED_TAG_KEY in self.tags():
            return
        if verdict is not None and verdict.needs_user:
            return
        note: str | None = None
        score = ended.score
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
        self, project: str, turn: TurnOutcome, ended: TurnEnded
    ) -> None:
        if self.on_finished is None:
            return
        # Delivering to the coordinator can wait for its whole agent step;
        # keep it off the tag-write chain.
        callback = self.on_finished

        async def notify() -> None:
            starter = await self._starter(ended.sender, ended.sender_known)
            await callback(project, turn, ended.text, starter)

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
