"""Wire protocol for ``/sockets/session/{conversation_id}``.

This module defines the *wire* format for the session socket. It is
deliberately **not** built on :class:`~openhands.sdk.event.base.Event`.

Why a separate hierarchy
------------------------
The legacy ``/sockets/events/{id}`` endpoint sends ``event.model_dump(...)``
(``sockets.py``) while the event log persists ``event.model_dump_json(...)``
(``event_store.py``) and the Python client decodes with ``Event.model_validate``
(``remote_conversation.py``). One class, one serialization, three roles — and
``Event`` is ``extra="forbid"``, so every additive wire field is a storage
migration and every storage change is a wire break. That coupling is why a
token delta cannot carry a stream identity today.

Here the ``Event`` rides *inside* an envelope as a payload and is not modified
in any way: the same class, the same ``extra="forbid"``, the same bytes on
disk. Everything the protocol needs — sequence number, stream identity,
progress — lives on the envelope, where adding a field costs nothing.

Versioning
----------
There is no handshake and no ``protocol`` field: **the URL is the protocol
version**. A client speaks ``/sockets/events/{id}`` or
``/sockets/session/{id}``, never both, so there is no dual-decode path to
maintain and no legacy client to negotiate with.

Frames, and the one rule that makes them work
---------------------------------------------
``ItemStarted`` announces a stream and mints nothing — the ``item_id`` *is*
the ``id`` of the ``Event`` that will eventually carry the finished message.
So a client holding an open slot retires it on a single equality test::

    frame.event.id == slot.item_id

That is the entire close protocol. There is no close frame, no watermark and
no text comparison. A stream that dies without producing a message is retired
by ``ItemAborted`` instead; exactly one of the two always fires.

Delivery rules
--------------
1. ``Durable`` never goes missing *across a reconnect*. Within a connection it
   may be dropped, because the client resumes with ``after_seq`` and loses
   nothing — that permission is what lets the server drop a slow consumer
   instead of blocking the publisher on it.
2. ``Delta`` may be dropped freely. A gap marks the slot lossy; the real text
   arrives with the message.
3. Every ``ItemStarted`` is retired by exactly one ``Durable`` or one
   ``ItemAborted`` — including on cancellation, provider failure, policy
   rejection and unhandled exception.
4. Progress frames are **never replayed**. On reconnect a client throws every
   open slot away and waits; the message is coming on the cursor regardless.
5. No ordering is promised *between* two open items beyond the order their
   ``ItemStarted`` frames arrived in.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk import Event


# --------------------------------------------------------------------------
# Size limits
#
# PROVISIONAL. The design calls for the pending-byte cap to be *derived* from
# the observed frame-size distribution (a multiple of the largest legal frame)
# rather than picked. That distribution has not been measured yet, so these are
# placeholders with the right shape and the wrong numbers. Do not treat them as
# tuned; see the "reproductions and baselines" issue before relying on them.
# --------------------------------------------------------------------------

MAX_FRAME_BYTES = 4 * 1024 * 1024
"""Largest single frame the server will emit. A frame above this drops the
connection rather than the frame, because ``Durable`` must survive a reconnect
and the cursor makes reconnecting lossless."""

MAX_PENDING_BYTES = 4 * MAX_FRAME_BYTES
"""Per-connection write budget. Admission is synchronous: if a frame does not
fit, the connection is dropped. It is never queued and the publisher never
blocks."""


class SessionFrameBase(BaseModel):
    """Base for every frame on the session socket.

    Note the *absence* of ``extra="forbid"``. Unlike ``Event`` — where
    forbidding extras is a correctness property of the durable record — the
    envelope is meant to grow. Clients must ignore frame types and fields they
    do not recognize, which is what lets the server add both without a version
    bump.
    """

    model_config = ConfigDict(frozen=True)


class SyncFrame(SessionFrameBase):
    """Sent once, before any replay, describing the range about to be sent.

    A client uses ``through_seq`` to know when it has caught up with history
    and can stop showing a loading state. ``through_seq`` is ``None`` for an
    empty conversation.
    """

    type: Literal["sync"] = "sync"
    from_seq: int | None = Field(
        default=None,
        description=(
            "Exclusive lower bound of the replay range, echoing the client's "
            "`after_seq`. None means no history was requested."
        ),
    )
    through_seq: int | None = Field(
        default=None,
        description=(
            "Highest sequence number on disk at the moment the connection was "
            "established. Everything above it arrives live."
        ),
    )


class DurableFrame(SessionFrameBase):
    """One persisted event, after it is safely on disk.

    ``event`` is the existing ``Event`` JSON, byte-for-byte what the legacy
    endpoint sends and what the event log stores. ``seq`` is the event's index
    in the log — already on disk as the ``{idx:05d}`` component of
    ``event-{idx:05d}-{event_id}.json``, so exposing it needs no migration.
    """

    type: Literal["durable"] = "durable"
    seq: int
    event: Event


class TransientFrame(SessionFrameBase):
    """An event that is published but never persisted.

    The design's two-category model (durable vs. progress) has no home for
    these, but the event bus genuinely carries them — ``subscribe_to_events``
    pushes a synthetic ``ConversationStateUpdateEvent`` snapshot on connect
    that is not in the event log. Giving them their own frame keeps ``seq`` on
    ``DurableFrame`` honest: if a frame has a ``seq``, that sequence number is
    real and resumable.

    A client should render these but must never use them to advance its cursor.
    """

    type: Literal["transient"] = "transient"
    event: Event


class ItemStartedFrame(SessionFrameBase):
    """A stream is opening. Sent once per attempt, before the first token.

    ``item_id`` is the ``Event.id`` the finished message will carry, minted at
    stream open. A higher ``attempt`` for the same ``item_id`` supersedes a
    lower one — that is how a retry announces itself, and it is the reason a
    re-streamed retry no longer looks like new text.

    ``anchor_seq`` is the sequence number the slot sits after in the
    transcript. It exists for exactly one reason: to stop a user message that
    lands mid-stream from splitting the bubble.
    """

    type: Literal["item_started"] = "item_started"
    item_id: str
    attempt: int = 1
    anchor_seq: int | None = None


class DeltaFrame(SessionFrameBase):
    """One masked increment of a stream.

    ``order`` is monotonic within (``item_id``, ``attempt``) and exists so a
    client can *detect* a gap, not repair one — a gap marks the slot lossy and
    the authoritative text arrives with the durable message.
    """

    type: Literal["delta"] = "delta"
    item_id: str
    attempt: int = 1
    order: int
    kind: Literal["text", "reasoning"] = "text"
    content: str


class ItemAbortedFrame(SessionFrameBase):
    """A stream ended without producing a message.

    This is what guarantees rule 3: every ``ItemStarted`` is retired. Without
    it a cancelled or failed stream strands an open slot in the client
    forever.
    """

    type: Literal["item_aborted"] = "item_aborted"
    item_id: str
    attempt: int = 1
    reason: str


class ErrorFrame(SessionFrameBase):
    """A connection-level problem.

    Distinct from ``ConversationErrorEvent``, which is a durable fact about the
    conversation. This is a fact about *this socket* and is never persisted.
    """

    type: Literal["error"] = "error"
    code: str
    detail: str


SessionFrame = Annotated[
    SyncFrame
    | DurableFrame
    | TransientFrame
    | ItemStartedFrame
    | DeltaFrame
    | ItemAbortedFrame
    | ErrorFrame,
    Field(discriminator="type"),
]
