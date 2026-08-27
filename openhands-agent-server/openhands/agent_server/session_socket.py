"""``/sockets/session/{conversation_id}`` — the session socket.

The legacy ``/sockets/events/{id}`` endpoint ships the durable record itself
over the wire, subscribes *before* it replays history, and awaits the socket
from inside the publisher. This endpoint fixes those three things and is
otherwise deliberately boring:

* **The wire is not the disk.** Frames are ``session_protocol`` envelopes; the
  ``Event`` rides inside one as an untouched payload.
* **History and live traffic do not interleave.** The subscriber is registered
  in buffering mode, the high-water sequence number is read *after* that, and
  the buffer is flushed against it once replay is done. Nothing is lost and
  nothing is duplicated.
* **A slow consumer cannot wedge the publisher.** Admission is synchronous and
  byte-bounded; the socket is awaited only by this connection's single writer
  task. A connection that cannot keep up is dropped, not buffered, and the
  client resumes losslessly with ``after_seq``.

The legacy endpoint is untouched and keeps working. A client speaks one or the
other — the URL is the protocol version.

Not implemented here
--------------------
``ItemStarted`` / ``Delta`` / ``ItemAborted`` are defined in
``session_protocol`` and this endpoint will carry them, but nothing produces
them yet: that needs ``StreamContext`` in the SDK, at all four streaming entry
points (``Agent.step``, ``Agent.astep`` and both ACP equivalents). Until then
this endpoint carries durable traffic only, and ``StreamingDeltaEvent`` is
filtered out rather than forwarded — deltas do not belong on the durable
channel, and forwarding them would recreate the coupling this endpoint exists
to remove.
"""

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from openhands.agent_server.conversation_service import (
    CredentialBindingActivationRequired,
)
from openhands.agent_server.pub_sub import MaxSubscribersError, Subscriber
from openhands.agent_server.session_protocol import (
    MAX_FRAME_BYTES,
    MAX_PENDING_BYTES,
    DurableFrame,
    ErrorFrame,
    SessionFrameBase,
    SyncFrame,
    TransientFrame,
)
from openhands.agent_server.sockets import (
    _accept_authenticated_websocket,
    _get_conversation_service,
    _is_auth_control_message,
    _is_websocket_connected,
    _safe_close_websocket,
)
from openhands.sdk import Event, Message
from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event import StreamingDeltaEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent


session_router = APIRouter(prefix="/sockets", tags=["WebSockets"])
logger = logging.getLogger(__name__)

REPLAY_PAGE_SIZE = 100
"""Events read from disk per executor hop. The loop yields between pages so a
long history cannot starve the event loop."""


# --------------------------------------------------------------------------
# One writer per connection, bounded in bytes
# --------------------------------------------------------------------------


@dataclass
class _ConnectionWriter:
    """Byte-bounded, non-blocking admission in front of a single writer task.

    ``send`` is **synchronous on purpose**. It is called from the pub/sub fan-out,
    and the entire point is that the fan-out never awaits this socket: today
    ``_WebSocketSubscriber.__call__`` awaits ``websocket.send_json`` directly,
    so one wedged browser tab stalls ``PubSub.__call__``'s ``gather`` and, with
    it, the publisher.

    Ordering comes from the queue plus exactly one draining task, so frames
    leave in the order they were admitted without any per-frame locking.

    Overflow drops the *connection*, never a frame. Silently discarding a
    ``Durable`` frame would break the one guarantee clients rely on; dropping
    the connection is safe because the client reconnects with ``after_seq`` and
    loses nothing. Disconnection is the backpressure mechanism.
    """

    websocket: WebSocket
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_pending_bytes: int = MAX_PENDING_BYTES

    closed_event: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    drop_reason: str | None = field(default=None, init=False)

    _queue: deque[tuple[str, int]] = field(default_factory=deque, init=False)
    _wake: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _pending_bytes: int = field(default=0, init=False)
    _task: asyncio.Task | None = field(default=None, init=False)

    @property
    def closed(self) -> bool:
        return self.closed_event.is_set()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def send(self, frame: SessionFrameBase) -> bool:
        """Admit one frame. Returns False if the connection is finished.

        Never blocks and never awaits.
        """
        if self.closed:
            return False

        payload = frame.model_dump_json(exclude_none=True)
        size = len(payload.encode("utf-8"))

        if size > self.max_frame_bytes:
            # Recoverable: the client comes back with its cursor and this event
            # is re-sent from disk. Nothing is lost, but it is worth shouting
            # about because it means a frame budget needs revisiting.
            logger.warning(
                "session_socket_frame_too_large: %d bytes > %d",
                size,
                self.max_frame_bytes,
            )
            self._fail("frame_too_large")
            return False

        if self._pending_bytes + size > self.max_pending_bytes:
            logger.info(
                "session_socket_slow_consumer: pending=%d incoming=%d budget=%d",
                self._pending_bytes,
                size,
                self.max_pending_bytes,
            )
            self._fail("slow_consumer")
            return False

        self._queue.append((payload, size))
        self._pending_bytes += size
        self._wake.set()
        return True

    def _fail(self, reason: str) -> None:
        if not self.closed:
            self.drop_reason = reason
            self.closed_event.set()
        self._wake.set()

    async def _run(self) -> None:
        try:
            while not self.closed:
                await self._wake.wait()
                self._wake.clear()
                while self._queue:
                    if self.closed:
                        return
                    payload, size = self._queue.popleft()
                    self._pending_bytes -= size
                    if not _is_websocket_connected(self.websocket):
                        self._fail("disconnected")
                        return
                    await self.websocket.send_text(payload)
        except asyncio.CancelledError:
            raise
        except (RuntimeError, WebSocketDisconnect):
            # Expected race: peer went away between the state check and the send.
            self._fail("disconnected")
        except Exception:
            logger.exception("session_socket_writer_error", stack_info=True)
            self._fail("writer_error")

    async def aclose(self) -> None:
        self._fail("closing")
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# --------------------------------------------------------------------------
# Subscriber: buffers, then goes live
# --------------------------------------------------------------------------


@dataclass
class _SessionSubscriber(Subscriber[Event]):
    """Converts events to frames and hands them to the writer.

    Starts in buffering mode: events are held privately and nothing is sent,
    so history can be replayed underneath without interleaving. ``go_live``
    flushes the buffer against the replay mark and switches to pass-through.
    """

    writer: _ConnectionWriter
    events: EventLog

    _buffer: list[Event] | None = field(default_factory=list, init=False)

    async def __call__(self, event: Event) -> None:
        if isinstance(event, StreamingDeltaEvent):
            # Deltas never ride the durable channel. Progress frames come from
            # StreamContext once it exists; forwarding a StreamingDeltaEvent
            # here would put the disk record back on the wire.
            return
        if self._buffer is not None:
            self._buffer.append(event)
            return
        self._emit(event)

    def _emit(self, event: Event) -> None:
        seq = self._seq_of(event)
        frame: SessionFrameBase = (
            TransientFrame(event=event)
            if seq is None
            else DurableFrame(seq=seq, event=event)
        )
        self.writer.send(frame)

    def _seq_of(self, event: Event) -> int | None:
        """The event's index in the log, or None if it is not persisted.

        ``ConversationStateUpdateEvent`` is published but never appended — the
        snapshot pushed by ``subscribe_to_events`` is synthesised on the fly —
        so it is transient by construction rather than by lookup failure.

        A lookup failure for anything else means the event was published before
        it was appended. That is possible today because the callback that
        publishes is composed *ahead* of the one that persists, so the index
        may not exist yet. It degrades to a transient frame (the client still
        renders the content, and gets the durable copy with its ``seq`` on the
        next reconnect) and is logged, because it should not happen once
        publish moves after persist.
        """
        if isinstance(event, ConversationStateUpdateEvent):
            return None
        try:
            return self.events.get_index(event.id)
        except KeyError:
            logger.warning(
                "session_socket_event_published_before_persist: kind=%s id=%s",
                type(event).__name__,
                event.id,
            )
            return None

    def go_live(self, through_seq: int | None) -> None:
        """Flush the buffer, dropping anything already replayed from disk."""
        buffered, self._buffer = self._buffer, None
        for event in buffered or ():
            seq = self._seq_of(event)
            if seq is not None and through_seq is not None and seq <= through_seq:
                continue  # already sent from disk during replay
            self._emit(event)


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def _read_page(events: EventLog, start: int, stop: int) -> list[tuple[int, Event]]:
    """Read one page of events by index, skipping unreadable ones.

    Mirrors the tolerance of ``EventService._get_searchable_event``: a corrupt
    or half-written file should cost one event, not the whole connection.
    """
    page: list[tuple[int, Event]] = []
    for idx in range(start, stop):
        try:
            page.append((idx, events[idx]))
        except (FileNotFoundError, UnicodeDecodeError, ValidationError, IndexError):
            logger.warning("session_socket_skipping_unreadable_event: idx=%d", idx)
    return page


async def _replay(
    events: EventLog, start: int, stop: int, writer: _ConnectionWriter
) -> bool:
    """Send ``[start, stop)`` from disk, in pages, yielding between them."""
    loop = asyncio.get_running_loop()
    for page_start in range(start, stop, REPLAY_PAGE_SIZE):
        page_stop = min(page_start + REPLAY_PAGE_SIZE, stop)
        page = await loop.run_in_executor(
            None, _read_page, events, page_start, page_stop
        )
        for seq, event in page:
            if not writer.send(DurableFrame(seq=seq, event=event)):
                return False
        await asyncio.sleep(0)
    return True


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@session_router.websocket("/session/{conversation_id}")
async def session_socket(
    conversation_id: UUID,
    websocket: WebSocket,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    after_seq: Annotated[
        int | None,
        Query(
            description=(
                "Resume cursor. Send everything with seq > after_seq before "
                "going live. Omit for live-only; use -1 for the full history. "
                "This replaces the legacy resend_mode/after_timestamp pair, "
                "which compared naive local timestamps and could not express "
                "'exactly where I left off'."
            )
        ),
    ] = None,
):
    """Session socket: durable events in an envelope, on one ordered channel."""
    if not await _accept_authenticated_websocket(websocket, session_api_key):
        return

    logger.info("session_socket_connected: %s after_seq=%s", conversation_id, after_seq)
    conv_service = _get_conversation_service(websocket)
    try:
        event_service = await conv_service.get_event_service(conversation_id)
    except CredentialBindingActivationRequired:
        await _safe_close_websocket(
            websocket, code=1013, reason="credential_binding_activation_required"
        )
        return
    if event_service is None:
        logger.warning("session_socket_conversation_not_found: %s", conversation_id)
        await _safe_close_websocket(
            websocket, code=4004, reason="Conversation not found"
        )
        return

    events = event_service.get_conversation().state.events

    writer = _ConnectionWriter(websocket)
    writer.start()
    subscriber = _SessionSubscriber(writer=writer, events=events)

    # --- the atomic replay boundary -------------------------------------
    #
    # Subscribe FIRST (buffering), then read the mark. That ordering is what
    # makes this lossless, and it needs no lock:
    #
    #   * appended before subscribe -> on disk before the mark is read, so
    #     mark >= seq and replay covers it;
    #   * appended after subscribe  -> in the buffer; if seq <= mark it is also
    #     replayed and the buffered copy is dropped by seq, otherwise the
    #     buffer is the only copy and the flush delivers it.
    #
    # The design called for taking the state lock across both steps. It is not
    # required, and skipping it keeps one more consumer off a FIFOLock that
    # already guards six unrelated things.
    try:
        subscriber_id = await event_service.subscribe_to_events(subscriber)
    except MaxSubscribersError:
        logger.warning("session_socket_subscriber_limit: %s", conversation_id)
        await writer.aclose()
        await _safe_close_websocket(
            websocket, code=1013, reason="Too many connections for this conversation"
        )
        return

    try:
        length = len(events)
        through_seq = length - 1 if length else None

        if not writer.send(SyncFrame(from_seq=after_seq, through_seq=through_seq)):
            return

        if after_seq is not None and through_seq is not None:
            start = max(after_seq + 1, 0)
            if not await _replay(events, start, through_seq + 1, writer):
                return

        subscriber.go_live(through_seq)

        await _inbound_loop(conversation_id, websocket, event_service, writer)
    finally:
        await event_service.unsubscribe_from_events(subscriber_id)
        await writer.aclose()
        if writer.drop_reason in ("slow_consumer", "frame_too_large"):
            await _safe_close_websocket(websocket, code=1013, reason=writer.drop_reason)
        logger.info(
            "session_socket_closed: %s reason=%s", conversation_id, writer.drop_reason
        )


async def _inbound_loop(
    conversation_id: UUID,
    websocket: WebSocket,
    event_service,
    writer: _ConnectionWriter,
) -> None:
    """Read client messages until the peer or the writer gives up.

    The writer failing has to end the connection too, so the receive is raced
    against it — otherwise a dropped slow consumer would sit here forever
    holding a subscription.
    """
    closed_waiter = asyncio.ensure_future(writer.closed_event.wait())
    try:
        while True:
            receiver = asyncio.ensure_future(websocket.receive_json())
            done, _ = await asyncio.wait(
                {receiver, closed_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            if closed_waiter in done:
                receiver.cancel()
                return
            try:
                data = receiver.result()
            except WebSocketDisconnect:
                logger.info("session_socket_disconnected: %s", conversation_id)
                return

            if _is_auth_control_message(data):
                continue

            try:
                message = Message.model_validate(data)
                await event_service.send_message(message, True)
            except WebSocketDisconnect:
                return
            except Exception as e:
                # A bad inbound message is not a reason to drop the connection,
                # and the error is about this socket rather than about the
                # conversation, so it is an ErrorFrame and not a durable event.
                logger.exception("session_socket_inbound_error", stack_info=True)
                if not writer.send(
                    ErrorFrame(code=e.__class__.__name__, detail=str(e))
                ):
                    return
    finally:
        closed_waiter.cancel()
