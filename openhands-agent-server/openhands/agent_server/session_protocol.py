"""Wire protocol for ``/sockets/session/{conversation_id}``.

Not built on ``Event``: the legacy endpoint sends the same class it persists
and validates, and ``Event`` is ``extra="forbid"``, so every wire field would
be a storage migration. Here ``Event`` rides inside an envelope, untouched,
and protocol fields live on the envelope.

The URL is the protocol version — no handshake, no ``protocol`` field.

Delivery rules:

1. ``Durable`` survives a reconnect via ``after_seq``; within a connection it
   may be dropped, which is what lets a slow consumer be disconnected instead
   of blocking the publisher.
2. ``Delta`` may be dropped freely; a gap marks the slot lossy and the real
   text arrives with the durable message.
3. Every ``ItemStarted`` is retired by exactly one ``Durable`` or
   ``ItemAborted``. ``item_id`` *is* the finished message's ``Event.id``, so a
   client closes a slot on ``frame.event.id == slot.item_id``.
4. Progress frames are never replayed.
5. No ordering is promised between two open items.
"""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from openhands.sdk import Event


# PROVISIONAL. The cap is meant to be derived from a measured frame-size
# distribution, which has not been done yet. Do not treat these as tuned.

MAX_FRAME_BYTES: Final[int] = 4 * 1024 * 1024
"""Largest frame the server will emit; a bigger one drops the connection."""

MAX_PENDING_BYTES: Final[int] = 4 * MAX_FRAME_BYTES
"""Per-connection write budget. Over it, the connection is dropped."""


class SessionFrameBase(BaseModel):
    """Base for every frame.

    Deliberately not ``extra="forbid"``: the envelope is meant to grow, and
    clients must ignore what they do not recognize.
    """

    model_config = ConfigDict(frozen=True)


class SyncFrame(SessionFrameBase):
    """Sent once, before any replay, describing the range about to be sent."""

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
            "Highest sequence number on disk when the connection was "
            "established; everything above it arrives live. None if empty."
        ),
    )


class DurableFrame(SessionFrameBase):
    """One persisted event, after it is safely on disk.

    ``seq`` is its index in the log, already on disk as the ``{idx:05d}``
    filename component, so exposing it needs no migration.
    """

    type: Literal["durable"] = "durable"
    seq: int
    event: Event


class TransientFrame(SessionFrameBase):
    """An event that is published but never persisted.

    Keeps ``seq`` honest: if a frame has one, it is real and resumable. A
    client renders these but must not advance its cursor on them.
    """

    type: Literal["transient"] = "transient"
    event: Event


class ItemStartedFrame(SessionFrameBase):
    """A stream is opening. Sent once per attempt, before the first token.

    A higher ``attempt`` for the same ``item_id`` supersedes a lower one, so a
    re-streamed retry does not read as new text. ``anchor_seq`` is the seq the
    slot sits after, so a user message landing mid-stream cannot split it.
    """

    type: Literal["item_started"] = "item_started"
    item_id: str
    attempt: int = 1
    anchor_seq: int | None = None


class DeltaFrame(SessionFrameBase):
    """One masked increment of a stream.

    ``order`` is monotonic within (``item_id``, ``attempt``) so a client can
    detect a gap, not repair one.
    """

    type: Literal["delta"] = "delta"
    item_id: str
    attempt: int = 1
    order: int
    kind: Literal["text", "reasoning"] = "text"
    content: str


class ItemAbortedFrame(SessionFrameBase):
    """A stream ended without producing a message.

    Without it, a cancelled or failed stream strands an open slot forever.
    """

    type: Literal["item_aborted"] = "item_aborted"
    item_id: str
    attempt: int = 1
    reason: str


class ErrorFrame(SessionFrameBase):
    """A problem with this socket, never persisted.

    Distinct from ``ConversationErrorEvent``, a durable fact about the
    conversation.
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
