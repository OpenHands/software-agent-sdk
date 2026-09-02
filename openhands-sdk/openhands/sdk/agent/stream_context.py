"""Stream identity for one agent step.

A streamed turn leaves the agent on two channels: token deltas, published as
they arrive, and the durable event, appended when the turn resolves. Nothing
linked them, so a client had to guess which deltas belonged to which message.

``StreamContext`` mints the identity up front::

    with StreamContext.open(conversation, on_token) as stream:
        ...                                 # deltas reference stream.item_id
        MessageEvent(id=stream.claim(), …)  # the message *is* the item

Minting is not a write. ``Event.id`` is already client-minted in-process
(``event/base.py``, ``default_factory=lambda: str(uuid.uuid4())``); the log
never assigns ids, it receives events that already have one. This changes only
*when* ``uuid4()`` runs. If the stream dies the id is simply never used and
nothing on disk referenced it.

Two invariants make the identity usable:

* **Every opened stream closes.** ``__exit__`` emits exactly one
  ``StreamAborted`` unless a durable event claimed the id, so cancellation, a
  provider failure, a policy rejection or an unhandled exception can no longer
  strand an open slot on a client.
* **A stream opens lazily.** ``StreamStarted`` is emitted on the first delta,
  not at ``open()``, so a step that never streams — an early condensation
  return, a non-streaming model — has nothing to abort.

The context is step-scoped: progress is never replayed, so there is nothing to
keep consistent across a reconnect or a restart, and it needs no home on the
conversation and no server-side registry of open items.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StreamStarted:
    """A stream is opening. One per attempt, before that attempt's first token.

    ``anchor_seq`` is the sequence number the slot sits after, so a user
    message landing mid-stream cannot split it.
    """

    item_id: str
    attempt: int
    anchor_seq: int | None


@dataclass(frozen=True, slots=True)
class StreamDelta:
    """One masked increment of a stream.

    ``order`` is monotonic within (``item_id``, ``attempt``) so a client can
    detect a gap, not repair one. ``chunk_id`` and ``choice_index`` are the
    provider's own identity, carried through as corroboration only: litellm
    issues a new completion id per retry attempt, so it cannot serve as the
    durable id.
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
        mask: Callable[[str], str],
    ) -> None:
        self.item_id = item_id
        self._anchor_seq = anchor_seq
        self._on_token = on_token
        self._on_stream = on_stream
        self._mask = mask
        self._attempt = 1
        self._order = itertools.count()
        self._started = False
        self._opened = False
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
            mask=state.secret_registry.compile_output_mask(),
        )

    @property
    def token_callback(self) -> Callable[[Any], None] | None:
        """The callback to hand the LLM, or ``None`` when nothing consumes it.

        ``llm.completion`` degrades a ``stream=True`` model to a non-streaming
        call when ``on_token`` is ``None`` (#4014). Substituting an
        unconditional wrapper here would silently take that fallback away.
        """
        if self._on_token is None and self._on_stream is None:
            return None
        return self.on_chunk

    def on_chunk(self, chunk: Any) -> None:
        """Forward a raw chunk downstream, then emit its stamped deltas.

        The pass-through keeps ``on_token`` consumers — the CLI, the legacy
        socket, user callbacks — receiving exactly what they receive today.
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

    def claim(self) -> str | None:
        """Take the minted id for a durable event, retiring the slot.

        Returns ``None`` once the id has been taken, so a second durable event
        in the same step keeps its own id rather than colliding.
        """
        if self._claimed or self._on_stream is None:
            return None
        self._claimed = True
        return self.item_id

    def close(self, reason: str) -> None:
        """Emit ``StreamAborted`` unless a durable event claimed the id.

        Keyed on whether the item was ever opened, not on the current attempt:
        a retry that dies before its first token still owes the client an
        answer for the attempt that did stream.
        """
        if not self._opened or self._claimed:
            return
        self._claimed = True
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
        self._emit(
            StreamDelta(
                item_id=self.item_id,
                attempt=self._attempt,
                order=next(self._order),
                kind=kind,
                content=self._mask(text),
                chunk_id=chunk_id,
                choice_index=choice_index,
            )
        )

    def _emit(self, frame: StreamProgress) -> None:
        """Never raises: ``close()`` runs from ``__exit__``, where an escaping
        sink error would replace the exception the step is unwinding."""
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
    chunk: Any,
) -> list[tuple[Literal["text", "reasoning"], str, str | None, int | None]]:
    """Break one token-callback payload into the deltas it carries.

    The ACP bridge hands ``on_token`` a bare ``str`` and never enters the LLM
    layer, which is why this lives above both rather than in ``llm.py``.
    """
    if isinstance(chunk, str):
        return [("text", chunk, None, None)] if chunk else []

    chunk_id = getattr(chunk, "id", None)
    out: list[tuple[Literal["text", "reasoning"], str, str | None, int | None]] = []
    for choice in getattr(chunk, "choices", None) or ():
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        index = getattr(choice, "index", None)
        reasoning = getattr(delta, "reasoning_content", None)
        if isinstance(reasoning, str) and reasoning:
            out.append(("reasoning", reasoning, chunk_id, index))
        content = getattr(delta, "content", None)
        if isinstance(content, str) and content:
            out.append(("text", content, chunk_id, index))
    return out
