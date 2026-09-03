"""``/sockets/session/{conversation_id}`` — the session socket.

Fixes three things the legacy ``/sockets/events/{id}`` endpoint gets wrong,
without touching it: frames are envelopes rather than the disk record; history
and live traffic cannot interleave; and a slow consumer cannot wedge the
publisher, because admission is byte-bounded and only this connection's writer
task awaits the socket.

``ItemStarted`` / ``Delta`` / ``ItemAborted`` are carried but never produced
yet — that needs ``StreamContext`` (#4682). Until then this is a durable-only
channel and ``StreamingDeltaEvent`` is dropped rather than forwarded, since
putting it back on the wire would restore the coupling this endpoint removes.
"""

import asyncio
import contextlib
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Annotated, Final
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

REPLAY_PAGE_SIZE: Final[int] = 100
"""Events read from disk per executor hop."""


@dataclass(slots=True)
class _ConnectionWriter:
    """Byte-bounded, non-blocking admission in front of a single writer task.

    ``send`` is synchronous on purpose: it runs in the pub/sub fan-out, which
    must never await this socket or one wedged tab stalls the publisher.
    Ordering comes from the queue plus exactly one draining task.

    Overflow drops the connection, never a frame — discarding a ``Durable``
    would break the guarantee clients rely on, whereas a reconnect with
    ``after_seq`` loses nothing. Disconnection is the backpressure.
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
        """Admit one frame, never blocking. False if the connection is done."""
        if self.closed:
            return False

        payload = frame.model_dump_json(exclude_none=True)
        size = len(payload.encode("utf-8"))

        if size > self.max_frame_bytes:
            # Worth shouting about: it means the frame budget needs revisiting.
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
            # Peer went away between the state check and the send.
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
            except asyncio.CancelledError:
                # Swallow only the cancellation we asked for; one aimed at this
                # task means shutdown and must not be absorbed.
                if not task.cancelled():
                    raise
            except Exception:
                pass


@dataclass(slots=True)
class _SessionSubscriber(Subscriber[Event]):
    """Converts events to frames and hands them to the writer.

    Starts buffering so history can replay underneath without interleaving;
    ``go_live`` flushes and switches to pass-through.
    """

    writer: _ConnectionWriter
    events: EventLog

    _buffer: list[Event] | None = field(default_factory=list, init=False)

    async def __call__(self, event: Event) -> None:
        if isinstance(event, StreamingDeltaEvent):
            # Deltas never ride the durable channel; progress frames will come
            # from StreamContext instead.
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

        By lookup, never by type: most ``ConversationStateUpdateEvent`` *are*
        appended, so treating the type as transient would strip their ``seq``
        and defeat the dedupe in ``go_live``. The one genuinely unpersisted
        event is the snapshot ``subscribe_to_events`` synthesises on connect,
        so only anything *else* missing is worth warning about.
        """
        try:
            return self.events.get_index(event.id)
        except KeyError:
            if not isinstance(event, ConversationStateUpdateEvent):
                logger.warning(
                    "session_socket_event_published_before_persist: kind=%s id=%s",
                    type(event).__name__,
                    event.id,
                )
            return None

    def go_live(self, through_seq: int | None) -> None:
        """Flush the buffer, dropping anything already replayed from disk."""
        buffered = self._buffer
        self._buffer = None
        for event in buffered or ():
            seq = self._seq_of(event)
            if seq is not None and through_seq is not None and seq <= through_seq:
                continue  # already sent from disk during replay
            self._emit(event)


def _read_page(events: EventLog, start: int, stop: int) -> list[tuple[int, Event]]:
    """Read one page by index, skipping unreadable events.

    A corrupt or half-written file should cost one event, not the connection —
    same tolerance as ``EventService._get_searchable_event``.
    """
    page: list[tuple[int, Event]] = []
    for idx in range(start, stop):
        try:
            page.append((idx, events[idx]))
        except (OSError, UnicodeDecodeError, ValidationError, IndexError):
            # OSError also covers the transient read errors EventLog warns
            # about on networked filesystems.
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
            # Per frame, not per page: only the writer task drains pending
            # bytes, and a page of large events can otherwise blow the budget
            # and drop a client that was never slow.
            await asyncio.sleep(0)
    return True


@session_router.websocket("/session/{conversation_id}")
async def session_socket(
    conversation_id: UUID,
    websocket: WebSocket,
    session_api_key: Annotated[str | None, Query(alias="session_api_key")] = None,
    after_seq: Annotated[
        int | None,
        Query(
            description=(
                "Resume cursor: everything with seq > after_seq is sent "
                "before going live. Omit for live-only, -1 for full history. "
                "Replaces the legacy resend_mode/after_timestamp pair, which "
                "compared naive local timestamps."
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

    try:
        events = event_service.get_conversation().state.events
    except ValueError:
        # Closed between the lookup above and here.
        logger.warning("session_socket_conversation_inactive: %s", conversation_id)
        await _safe_close_websocket(
            websocket, code=4004, reason="Conversation not found"
        )
        return

    writer = _ConnectionWriter(websocket)
    writer.start()
    subscriber = _SessionSubscriber(writer=writer, events=events)

    # Subscribe FIRST (buffering), then read the mark: no lock needed, since
    # anything appended before subscribe is on disk before the mark is read,
    # and anything after lands in the buffer and is deduped by seq.
    try:
        subscriber_id = await event_service.subscribe_to_events(subscriber)
    except MaxSubscribersError:
        logger.warning("session_socket_subscriber_limit: %s", conversation_id)
        await writer.aclose()
        await _safe_close_websocket(
            websocket, code=1013, reason="Too many connections for this conversation"
        )
        return
    except Exception:
        # subscribe_to_events registers before it can fail, and this lands
        # before the try/finally below, so clean up the writer and socket here.
        # The registration itself can't be reclaimed (the id is only returned
        # on success); it is inert but holds a slot. Belongs in
        # subscribe_to_events, which has the same hole for the legacy endpoint.
        logger.exception("session_socket_subscribe_failed: %s", conversation_id)
        await writer.aclose()
        await _safe_close_websocket(websocket, code=1011, reason="subscribe_failed")
        return

    try:
        length = len(events)
        through_seq = length - 1 if length else None

        if not writer.send(SyncFrame(from_seq=after_seq, through_seq=through_seq)):
            return

        replayed = False
        if after_seq is not None and through_seq is not None:
            start = max(after_seq + 1, 0)
            if not await _replay(events, start, through_seq + 1, writer):
                return
            replayed = True

        # Dedupe against the mark only if replay actually sent that range: a
        # live-only client got nothing from disk, and has no cursor to recover
        # whatever the mark would discard.
        subscriber.go_live(through_seq if replayed else None)

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

    Raced against the writer failing, or a dropped slow consumer would sit
    here forever holding a subscription.
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
                if receiver in done:
                    # Both finished at once: cancel() is a no-op on a done
                    # future, so consume the outcome or asyncio logs
                    # "Task exception was never retrieved" on GC.
                    with contextlib.suppress(Exception, asyncio.CancelledError):
                        receiver.result()
                return
            try:
                data = receiver.result()
            except WebSocketDisconnect:
                logger.info("session_socket_disconnected: %s", conversation_id)
                return
            except Exception as e:
                # receive_json decodes with json.loads, so a non-JSON frame
                # raises here, not at validation below. Keep the connection,
                # as the legacy endpoint's one broad handler does.
                logger.warning(
                    "session_socket_bad_inbound_frame: %s: %s",
                    e.__class__.__name__,
                    e,
                )
                if not writer.send(
                    ErrorFrame(code=e.__class__.__name__, detail=str(e))
                ):
                    return
                continue

            if _is_auth_control_message(data):
                continue

            try:
                message = Message.model_validate(data)
                await event_service.send_message(message, True)
            except WebSocketDisconnect:
                return
            except Exception as e:
                # About this socket, not the conversation, so an ErrorFrame
                # rather than a durable event — and not worth dropping over.
                logger.exception("session_socket_inbound_error", stack_info=True)
                if not writer.send(
                    ErrorFrame(code=e.__class__.__name__, detail=str(e))
                ):
                    return
    finally:
        closed_waiter.cancel()
