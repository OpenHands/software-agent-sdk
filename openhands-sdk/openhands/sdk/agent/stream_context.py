"""Stream identity for one agent step: mint it, stamp the deltas, close it once.

The durable event is built with the minted id, so a client retires its open
slot on ``frame.event.id == slot.item_id``.

Minting up front is safe against the append-only log because it is not a write:
``Event.id`` is already client-minted in-process (``event/base.py``), so this
changes only *when* ``uuid4()`` runs, and an unused id was never on disk.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openhands.sdk.conversation.secret_registry import StreamOutputMask
from openhands.sdk.llm.streaming import LLMStreamChunk
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamStarted:
    """A stream is opening. One per attempt, before its first token.

    ``anchor_seq`` is the seq the slot sits after, so a user message landing
    mid-stream cannot split it.
    """

    item_id: str
    attempt: int
    anchor_seq: int | None


@dataclass(frozen=True, slots=True)
class StreamDelta:
    """One masked increment of a stream.

    ``chunk_id`` is corroboration only: litellm mints a new completion id per
    retry attempt, so it cannot identify the durable event.
    """

    item_id: str
    attempt: int
    order: int
    kind: Literal["text", "reasoning"]
    content: str
    chunk_id: str | None = None
    choice_index: int | None = None


@dataclass(frozen=True, slots=True)
class StreamAborted:
    """A stream ended without producing a durable event."""

    item_id: str
    attempt: int
    reason: str


StreamProgress = StreamStarted | StreamDelta | StreamAborted
StreamProgressCallbackType = Callable[[StreamProgress], None]


class StreamContext:
    """One streaming slot: mints its id, stamps its deltas, closes it once."""

    def __init__(
        self,
        item_id: str,
        anchor_seq: int | None,
        on_token: Callable[[Any], None] | None,
        on_stream: StreamProgressCallbackType | None,
        mask: Callable[[], StreamOutputMask],
    ) -> None:
        self.item_id = item_id
        self._anchor_seq = anchor_seq
        self._on_token = on_token
        self._on_stream = on_stream
        self._mask = mask
        # One masker per kind: text and reasoning are separately ordered, and a
        # masker holds back a partial secret across the chunks of its own kind.
        self._masks: dict[Literal["text", "reasoning"], StreamOutputMask] = {}
        self._attempt = 1
        self._order = itertools.count()
        self._started = False
        self._opened = False
        self._reserved = False
        self._claimed = False
        self._chunk_id: str | None = None

    @classmethod
    def open(
        cls,
        conversation: LocalConversation,
        on_token: Callable[[Any], None] | None,
    ) -> StreamContext:
        """Mint an id and read the anchor. Neither touches the event log."""
        state = conversation.state
        length = len(state.events)
        return cls(
            item_id=str(uuid.uuid4()),
            anchor_seq=length - 1 if length else None,
            on_token=on_token,
            on_stream=conversation.on_stream,
            mask=state.secret_registry.compile_stream_mask,
        )

    @property
    def token_callback(self) -> Callable[[Any], None] | None:
        """The callback to hand the LLM, or ``None`` when nothing consumes it.

        ``llm.completion`` degrades a ``stream=True`` model to a non-streaming
        call when ``on_token`` is ``None`` (#4014); an unconditional wrapper
        would take that fallback away.
        """
        if self._on_token is None and self._on_stream is None:
            return None
        return self.on_chunk

    def on_chunk(self, chunk: Any) -> None:
        """Forward the raw chunk downstream, then emit its stamped deltas.

        The pass-through is what keeps existing ``on_token`` consumers seeing
        exactly what they see today.
        """
        if self._on_token is not None:
            self._on_token(chunk)
        if self._on_stream is None:
            return
        try:
            for kind, text, chunk_id, choice_index in _split_chunk(chunk):
                self._emit_delta(kind, text, chunk_id, choice_index)
        except Exception:
            # Progress is a UX affordance; never fail a turn over it.
            logger.debug("stream progress emission failed", exc_info=True)

    def new_attempt(self) -> None:
        """Start a new attempt for the same item; a higher one supersedes."""
        if not self._opened:
            return
        self._attempt += 1
        self._order = itertools.count()
        self._started = False
        self._chunk_id = None
        # The new attempt re-streams the item, so the old tail is superseded.
        self._masks.clear()

    def claim(self) -> str | None:
        """Reserve the minted id for a durable event being built.

        ``None`` once reserved, so a second durable event in the same step
        keeps its own id rather than colliding. Reserving does not retire the
        slot: an event that is never emitted still owes an abort, so the
        caller must :meth:`commit` once emission has succeeded.
        """
        if self._reserved or self._on_stream is None:
            return None
        self._flush()
        self._reserved = True
        return self.item_id

    def commit(self) -> None:
        """Retire the slot: the durable event carrying the id was emitted."""
        if self._reserved:
            self._claimed = True

    def close(self, reason: str) -> None:
        """Emit ``StreamAborted`` unless a durable event committed the id.

        Keyed on ever-opened, not on the current attempt: a retry that dies
        before its first token still owes an answer for the one that streamed.
        """
        if not self._opened or self._claimed:
            return
        self._claimed = True
        self._flush()
        self._emit(StreamAborted(self.item_id, self._attempt, reason))

    def __enter__(self) -> StreamContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        self.close(_abort_reason(exc_type))
        return False

    def _emit_delta(
        self,
        kind: Literal["text", "reasoning"],
        text: str,
        chunk_id: str | None,
        choice_index: int | None,
    ) -> None:
        if chunk_id is not None and self._chunk_id is not None:
            if chunk_id != self._chunk_id:
                # litellm mints a completion id per attempt, so a change of id
                # mid-item is a re-stream of the same slot.
                self.new_attempt()
        self._chunk_id = chunk_id
        if not self._started:
            self._started = self._opened = True
            self._emit(StreamStarted(self.item_id, self._attempt, self._anchor_seq))
        masker = self._masks.get(kind)
        if masker is None:
            masker = self._masks[kind] = self._mask()
        released = masker.feed(text)
        if released:
            self._emit_content(kind, released, chunk_id, choice_index)

    def _flush(self) -> None:
        """Release the tail each masker holds back against a split secret."""
        for kind, masker in self._masks.items():
            held = masker.flush()
            if held:
                self._emit_content(kind, held, self._chunk_id, None)
        self._masks.clear()

    def _emit_content(
        self,
        kind: Literal["text", "reasoning"],
        content: str,
        chunk_id: str | None,
        choice_index: int | None,
    ) -> None:
        self._emit(
            StreamDelta(
                item_id=self.item_id,
                attempt=self._attempt,
                order=next(self._order),
                kind=kind,
                content=content,
                chunk_id=chunk_id,
                choice_index=choice_index,
            )
        )

    def _emit(self, frame: StreamProgress) -> None:
        """Never raises: ``close()`` runs from ``__exit__``, where a sink error
        would replace the exception the step is unwinding."""
        assert self._on_stream is not None
        try:
            self._on_stream(frame)
        except Exception:
            logger.debug("stream progress sink failed", exc_info=True)


def _abort_reason(exc_type: type[BaseException] | None) -> str:
    if exc_type is None:
        return "no_durable_event"
    if issubclass(exc_type, (asyncio.CancelledError, KeyboardInterrupt)):
        return "cancelled"
    return exc_type.__name__


def _split_chunk(
    chunk: LLMStreamChunk | str,
) -> list[tuple[Literal["text", "reasoning"], str, str | None, int | None]]:
    """Break one token-callback payload into the deltas it carries.

    The ACP bridge passes a bare ``str`` and never enters the LLM layer, which
    is why this lives above both rather than in ``llm.py``.
    """
    if isinstance(chunk, str):
        return [("text", chunk, None, None)] if chunk else []

    out: list[tuple[Literal["text", "reasoning"], str, str | None, int | None]] = []
    for choice in chunk.choices or ():
        delta = choice.delta
        if delta is None:
            continue
        # getattr, not attribute access: litellm *deletes* reasoning_content
        # when the provider omits it, declared field or not.
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            out.append(("reasoning", reasoning, chunk.id, choice.index))
        if isinstance(delta.content, str) and delta.content:
            out.append(("text", delta.content, chunk.id, choice.index))
    return out
