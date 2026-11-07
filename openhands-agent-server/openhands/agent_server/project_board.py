"""Record how a project thread's turn ended, for the project board.

When a conversation in a project finishes a run, :class:`ThreadOutcomeSubscriber`
tags it with how the agent's last message ended (``turn=asked`` or
``turn=answered``) and, when the critic is confident the task succeeded, marks
it resolved (``resolved=auto``). A new user message removes both tags, since
the conversation is open again.

The board reads these tags from the conversation catalog, so listing a project
never loads conversation state or events.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from uuid import UUID

from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.conversation.title_utils import extract_message_text
from openhands.sdk.conversation.types import (
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
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.builtins.finish import FinishAction


logger = get_logger(__name__)

AUTO_RESOLVE_THRESHOLD: Final[float] = 0.9
"""Critic score at or above which a finished thread is marked resolved."""

TurnOutcome = Literal["asked", "answered"]
ResolvedBy = Literal["user", "auto"]

_EXECUTION_STATUS_KEY: Final[str] = "execution_status"
# A question mark that ends a sentence: followed by space, closing markup or
# quotes, or the end of the text. Leaves out "?" inside URLs and code.
_QUESTION_END = re.compile(r"\?(?=[\s*_)\]\"'`»”’]|$)")
_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.DOTALL)


SetTag = Callable[..., Awaitable[Any]]
"""``ConversationService.set_conversation_tag``-shaped callable."""


def classify_turn(text: str | None) -> TurnOutcome:
    """Say whether an agent's final message asks the person something.

    Looks at the last paragraph outside code blocks: it asks when a sentence
    there ends with a question mark. This is a heuristic; it errs towards
    ``answered``, which keeps a thread out of the "needs input" group.
    """
    if not text:
        return "answered"
    prose = _CODE_BLOCK.sub("", text).strip()
    if not prose:
        return "answered"
    last_paragraph = re.split(r"\n\s*\n", prose)[-1]
    return "asked" if _QUESTION_END.search(last_paragraph) else "answered"


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
class ThreadOutcomeSubscriber(Subscriber[Event]):
    """Tag a project conversation with how its runs end.

    ``tags`` returns the conversation's current tags; only conversations with a
    ``project`` tag are changed, so a conversation that joins a project later
    is covered from its next run on. Tag writes run in background tasks, one
    after another in event order, so publishing is never slowed down.
    """

    conversation_id: UUID
    tags: Callable[[], dict[str, str]]
    set_tag: SetTag
    _last_text: str | None = field(default=None, init=False)
    _last_score: float | None = field(default=None, init=False)
    _seen_agent_output: bool = field(default=False, init=False)
    _tail: asyncio.Task | None = field(default=None, init=False)

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
            if event.source == "user":
                self._reset()
                if PROJECT_TAG_KEY in self.tags():
                    self._enqueue(self._reopen)
            elif event.source == "agent":
                self._note_agent_output(extract_message_text(event), event)
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
            text, score = self._last_text, self._last_score
            self._reset()
            if PROJECT_TAG_KEY in self.tags():
                self._enqueue(lambda: self._record(text, score))

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
        await self.set_tag(self.conversation_id, TURN_TAG_KEY, None)
        await self.set_tag(self.conversation_id, RESOLVED_TAG_KEY, None)

    async def _record(self, text: str | None, score: float | None) -> None:
        await self.set_tag(self.conversation_id, TURN_TAG_KEY, classify_turn(text))
        if score is not None and score >= AUTO_RESOLVE_THRESHOLD:
            # Never overwrite the person's own resolve.
            await self.set_tag(
                self.conversation_id,
                RESOLVED_TAG_KEY,
                "auto",
                only_if_absent=True,
            )

    async def drain(self) -> None:
        """Wait for queued tag updates (used by tests and shutdown)."""
        if self._tail is not None:
            await asyncio.gather(self._tail, return_exceptions=True)
