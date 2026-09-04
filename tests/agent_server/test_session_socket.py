"""Session socket semantics: framing, the replay boundary, backpressure."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect

from openhands.agent_server.session_protocol import MAX_FRAME_BYTES
from openhands.agent_server.session_socket import (
    _ConnectionWriter,
    _inbound_loop,
    _read_page,
    _replay,
    _SessionSubscriber,
)
from openhands.sdk import Message, TextContent
from openhands.sdk.event import MessageEvent, StreamingDeltaEvent
from openhands.sdk.event.conversation_state import ConversationStateUpdateEvent


def _msg(text: str) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


class _FakeWebSocket:
    """Records what actually reached the wire, and can be made to hang."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.gate: asyncio.Event | None = None

    async def send_text(self, payload: str) -> None:
        if self.gate is not None:
            await self.gate.wait()
        self.sent.append(payload)

    def frames(self) -> list[dict]:
        return [json.loads(p) for p in self.sent]


class _FakeEventLog:
    """Enough EventLog for framing: index by id, read by index, length."""

    def __init__(self) -> None:
        self._events: list = []
        self._by_id: dict[str, int] = {}

    def add(self, event) -> int:
        idx = len(self._events)
        self._events.append(event)
        self._by_id[event.id] = idx
        return idx

    def get_index(self, event_id: str) -> int:
        try:
            return self._by_id[event_id]
        except KeyError:
            raise KeyError(f"Unknown event_id: {event_id}")

    def __getitem__(self, idx: int):
        return self._events[idx]

    def __len__(self) -> int:
        return len(self._events)


async def _drain(writer: _ConnectionWriter) -> None:
    """Let the writer task flush what has been admitted."""
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_durable_frame_carries_seq_and_untouched_event():
    ws, log = _FakeWebSocket(), _FakeEventLog()
    event = _msg("hello")
    log.add(event)

    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]
    sub.go_live(None)
    await sub(event)
    await _drain(writer)
    await writer.aclose()

    (frame,) = ws.frames()
    assert frame["type"] == "durable"
    assert frame["seq"] == 0
    # Byte-identical to what the legacy endpoint sends.
    assert frame["event"] == event.model_dump(mode="json", exclude_none=True)


@pytest.mark.asyncio
async def test_deltas_never_reach_the_durable_channel():
    """The bug that put token deltas on webhooks and telemetry."""
    ws, log = _FakeWebSocket(), _FakeEventLog()
    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]
    sub.go_live(None)

    await sub(StreamingDeltaEvent(content="tok"))
    await sub(StreamingDeltaEvent(reasoning_content="think"))
    await _drain(writer)
    await writer.aclose()

    assert ws.frames() == []


@pytest.mark.asyncio
async def test_unpersisted_event_is_transient_and_carries_no_seq():
    ws, log = _FakeWebSocket(), _FakeEventLog()
    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]
    sub.go_live(None)

    await sub(ConversationStateUpdateEvent(key="execution_status", value="idle"))
    await _drain(writer)
    await writer.aclose()

    (frame,) = ws.frames()
    assert frame["type"] == "transient"
    assert "seq" not in frame


@pytest.mark.asyncio
async def test_replay_and_live_do_not_interleave_or_duplicate():
    """Subscribe (buffering) -> read mark -> replay -> flush.

    An event landing during replay arrives exactly once, after the history.
    """
    ws, log = _FakeWebSocket(), _FakeEventLog()
    history = [_msg(f"h{i}") for i in range(3)]
    for e in history:
        log.add(e)

    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]

    through_seq = len(log) - 1

    # Two arrive mid-replay: one already on disk, one genuinely new.
    await sub(history[2])
    live = _msg("live")
    log.add(live)
    await sub(live)

    assert await _replay(log, 0, through_seq + 1, writer)  # type: ignore[arg-type]
    sub.go_live(through_seq)
    await _drain(writer)
    await writer.aclose()

    frames = ws.frames()
    assert [f["seq"] for f in frames] == [0, 1, 2, 3], "no gaps, no duplicates"
    assert [f["event"]["id"] for f in frames] == [e.id for e in [*history, live]]


@pytest.mark.asyncio
async def test_persisted_state_update_is_durable_and_deduped():
    """A ConversationStateUpdateEvent in the log must not be transient.

    append_event stores these, so deciding by type would strip their seq and
    defeat the dedupe — the event would arrive from replay and again from the
    buffer.
    """
    ws, log = _FakeWebSocket(), _FakeEventLog()
    state_update = ConversationStateUpdateEvent(key="execution_status", value="running")
    log.add(state_update)
    through_seq = len(log) - 1

    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]

    # Buffered mid-replay and also sent from disk: one copy must reach the wire.
    await sub(state_update)
    assert await _replay(log, 0, through_seq + 1, writer)  # type: ignore[arg-type]
    sub.go_live(through_seq)
    await _drain(writer)
    await writer.aclose()

    frames = ws.frames()
    assert [f["type"] for f in frames] == ["durable"], "sent twice, or sent transient"
    assert frames[0]["seq"] == 0


@pytest.mark.asyncio
async def test_live_only_connection_keeps_events_buffered_during_subscribe():
    """No replay ran, so nothing may be discarded as 'already replayed'.

    A client omitting ``after_seq`` has no cursor to recover what deduping
    against the mark would drop.
    """
    ws, log = _FakeWebSocket(), _FakeEventLog()
    for i in range(3):
        log.add(_msg(f"h{i}"))

    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]

    # Arrived during subscribe, so its seq is below the mark.
    raced = _msg("landed during subscribe")
    log.add(raced)
    await sub(raced)

    # Live-only: the endpoint passes None when no replay happened.
    sub.go_live(None)
    await _drain(writer)
    await writer.aclose()

    frames = ws.frames()
    assert [f["event"]["id"] for f in frames] == [raced.id]
    assert frames[0]["seq"] == 3


@pytest.mark.asyncio
async def test_wedged_connection_drops_instead_of_blocking_the_publisher():
    """Admission is synchronous: a stuck socket must never stall the caller.

    What the legacy endpoint gets wrong by awaiting send_json in its
    subscriber.
    """
    ws, log = _FakeWebSocket(), _FakeEventLog()
    ws.gate = asyncio.Event()  # accepts nothing until released

    writer = _ConnectionWriter(ws, max_pending_bytes=4096)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]
    sub.go_live(None)

    # Publish far past the budget, and time the publisher.
    started = asyncio.get_running_loop().time()
    for i in range(500):
        event = _msg(f"m{i}" * 50)
        log.add(event)
        await sub(event)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 0.5, "publisher blocked on a wedged consumer"
    assert writer.closed, "over-budget connection should be dropped"
    assert writer.drop_reason == "slow_consumer"

    ws.gate.set()
    await writer.aclose()


@pytest.mark.asyncio
async def test_oversized_frame_drops_the_connection_not_the_event():
    """Durable must survive a reconnect, so an unsendable frame kills the
    connection and the client resumes from its cursor."""
    ws, log = _FakeWebSocket(), _FakeEventLog()
    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()
    sub = _SessionSubscriber(writer=writer, events=log)  # type: ignore[arg-type]
    sub.go_live(None)

    huge = _msg("x" * (MAX_FRAME_BYTES + 1))
    log.add(huge)
    await sub(huge)
    await _drain(writer)

    assert writer.closed
    assert writer.drop_reason == "frame_too_large"
    assert ws.frames() == []
    await writer.aclose()


def test_replay_skips_unreadable_events_instead_of_failing():
    class _Corrupt(_FakeEventLog):
        def __getitem__(self, idx: int):
            if idx == 1:
                raise FileNotFoundError("half-written")
            return super().__getitem__(idx)

    log = _Corrupt()
    for i in range(3):
        log.add(_msg(f"e{i}"))

    page = _read_page(log, 0, 3)  # type: ignore[arg-type]
    assert [seq for seq, _ in page] == [0, 2]


@pytest.mark.asyncio
async def test_malformed_inbound_frame_is_reported_not_raised():
    """A non-JSON text frame must not escape the inbound loop.

    receive_json raises JSONDecodeError, not WebSocketDisconnect, so catching
    only the latter let one bad frame take down the connection.
    """
    ws = _FakeWebSocket()
    writer = _ConnectionWriter(ws)  # type: ignore[arg-type]
    writer.start()

    receives = 0

    async def receive_json():
        nonlocal receives
        receives += 1
        if receives == 1:
            raise json.JSONDecodeError("Expecting value", "not json", 0)
        raise WebSocketDisconnect()

    ws.receive_json = receive_json  # type: ignore[attr-defined]

    await asyncio.wait_for(
        _inbound_loop(uuid4(), ws, object(), writer),  # type: ignore[arg-type]
        timeout=5,
    )
    await _drain(writer)
    await writer.aclose()

    (frame,) = ws.frames()
    assert frame["type"] == "error"
    assert frame["code"] == "JSONDecodeError"
    # The loop kept going rather than unwinding.
    assert receives == 2
