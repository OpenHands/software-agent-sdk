"""Event sockets against a real EventService: replay/live races and teardown.

Events are emitted on the production path: ``_on_event`` under the state lock
persists, then ``AsyncCallbackWrapper`` schedules the pub/sub fan-out.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from pydantic import SecretStr
from starlette.websockets import WebSocketState

from openhands.agent_server import session_socket, sockets
from openhands.agent_server.config import Config
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import StoredConversation
from openhands.agent_server.pub_sub import PubSub, Subscriber
from openhands.sdk import Message, TextContent
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def _msg(text: str) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


class _FakeWS:
    """Enough of starlette's WebSocket for both event-socket handlers."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple | None = None
        self.disconnect = asyncio.Event()
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.headers: dict = {}
        self.app = SimpleNamespace(
            state=SimpleNamespace(config=Config(session_api_keys=[]))
        )

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)
        self.application_state = WebSocketState.DISCONNECTED
        # As uvicorn does: a pending receive() wakes with a disconnect.
        self.disconnect.set()

    async def send_json(self, data):
        self.sent.append(data)

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def receive_json(self):
        await self.disconnect.wait()
        self.client_state = WebSocketState.DISCONNECTED
        raise WebSocketDisconnect(1000)


def _texts(frames: list[dict], session: bool = False) -> list[str]:
    out = []
    for frame in frames:
        event = frame.get("event") if session else frame
        try:
            out.append(event["llm_message"]["content"][0]["text"])  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            continue
    return out


async def _wait_until(predicate, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def svc(tmp_path, monkeypatch):
    with patch("openhands.sdk.llm.utils.model_info.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        service = EventService(
            stored=StoredConversation(
                id=uuid4(),
                workspace=LocalWorkspace(working_dir=str(tmp_path / "workspace")),
            ),
            agent=Agent(
                llm=LLM(usage_id="t", model="test-model", api_key=SecretStr("k")),
                tools=[],
            ),
            conversations_dir=tmp_path / "conversations",
        )
        await service.start()

    async def get_event_service(_cid):
        return service

    monkeypatch.setattr(
        sockets,
        "conversation_service",
        SimpleNamespace(get_event_service=get_event_service),
    )
    yield service
    if service.is_open():
        await service.close()


def _emit_sync(svc: EventService, event) -> None:
    conversation = svc._conversation
    assert conversation is not None
    with conversation._state:
        conversation._on_event(event)


async def _emit(svc: EventService, event) -> None:
    await asyncio.to_thread(_emit_sync, svc, event)
    wrapper = svc._callback_wrapper
    assert wrapper is not None
    await asyncio.to_thread(wrapper.wait_for_pending, 5)


def _legacy(ws, svc, resend_mode=None):
    return sockets.events_socket(
        svc.stored.id,
        ws,
        session_api_key=None,
        resend_mode=resend_mode,
        after_timestamp=None,
        resend_all=False,
    )


def _session(ws, svc, after_seq=None):
    return session_socket.session_socket(
        svc.stored.id, ws, session_api_key=None, after_seq=after_seq
    )


async def _finish(ws, task):
    await asyncio.sleep(0.2)
    ws.disconnect.set()
    await task


# ------------------------------------------------------------- legacy replay


@pytest.mark.asyncio
async def test_legacy_replay_with_queued_publish_is_exactly_once_in_order(svc):
    """E1 is persisted and its fan-out still queued when the socket subscribes:
    it reaches the subscriber live and is also in the replay."""
    await _emit(svc, _msg("E0"))
    ws = _FakeWS()
    _emit_sync(svc, _msg("E1"))
    task = asyncio.create_task(_legacy(ws, svc, resend_mode="all"))
    await _wait_until(lambda: "E1" in _texts(ws.sent))
    await _finish(ws, task)
    assert _texts(ws.sent) == ["E0", "E1"]


@pytest.mark.asyncio
async def test_legacy_event_during_replay_follows_history_once(svc, monkeypatch):
    for i in range(3):
        await _emit(svc, _msg(f"E{i}"))
    gate, in_replay = asyncio.Event(), asyncio.Event()
    real_search = svc.search_events

    async def held_search(*args, **kwargs):
        in_replay.set()
        await gate.wait()
        return await real_search(*args, **kwargs)

    monkeypatch.setattr(svc, "search_events", held_search)
    ws = _FakeWS()
    task = asyncio.create_task(_legacy(ws, svc, resend_mode="all"))
    await in_replay.wait()
    await _emit(svc, _msg("E3"))  # after subscribe, before the replay reads
    gate.set()
    await _wait_until(lambda: "E3" in _texts(ws.sent))
    await _finish(ws, task)
    assert _texts(ws.sent) == ["E0", "E1", "E2", "E3"]
    # The connect snapshot still comes first.
    assert ws.sent[0]["kind"] == "ConversationStateUpdateEvent"


# ------------------------------------------------------------- subscribe failure


@pytest.mark.asyncio
async def test_failed_subscribe_releases_its_slot(svc, monkeypatch):
    baseline = set(svc._pub_sub.subscriber_ids())

    def boom():
        raise ValueError("inactive_service")

    monkeypatch.setattr(svc, "_create_initial_state_sync", boom)
    ws = _FakeWS()
    await _session(ws, svc)
    assert ws.closed == (1011, "subscribe_failed")
    with pytest.raises(ValueError):
        await _legacy(_FakeWS(), svc)
    assert svc._pub_sub.subscriber_ids() == baseline


# ------------------------------------------------------------- teardown


@pytest.mark.asyncio
async def test_closing_the_conversation_closes_its_sockets(svc):
    ws_legacy, ws_session = _FakeWS(), _FakeWS()
    t_legacy = asyncio.create_task(_legacy(ws_legacy, svc))
    t_session = asyncio.create_task(_session(ws_session, svc))
    await _wait_until(lambda: ws_legacy.sent and ws_session.sent)
    await svc.close()  # what delete_conversation and eviction do
    async with asyncio.timeout(5):
        await t_legacy
        await t_session
    assert ws_legacy.closed == (1001, "conversation_closed")
    assert ws_session.closed == (1001, "conversation_closed")


class _Sub(Subscriber[str]):
    def __init__(self, gate=None, raise_on_close=False):
        self.gate, self.raise_on_close, self.closed = gate, raise_on_close, False

    async def __call__(self, event):
        pass

    async def close(self):
        if self.gate:
            await self.gate.wait()
        self.closed = True
        if self.raise_on_close:
            raise ValueError("close failed")


@pytest.mark.asyncio
async def test_pubsub_close_closes_a_subscriber_added_meanwhile():
    pub_sub = PubSub[str](max_subscribers=50)
    gate = asyncio.Event()
    pub_sub.subscribe(_Sub(gate=gate))
    closing = asyncio.create_task(pub_sub.close())
    await asyncio.sleep(0)
    late = _Sub()
    pub_sub.subscribe(late)
    gate.set()
    await closing
    assert late.closed
    assert not pub_sub.subscriber_ids()


@pytest.mark.asyncio
async def test_pubsub_close_survives_a_failing_subscriber():
    pub_sub = PubSub[str](max_subscribers=50)
    ok = _Sub()
    pub_sub.subscribe(_Sub(raise_on_close=True))
    pub_sub.subscribe(ok)
    await pub_sub.close()
    assert ok.closed
    assert not pub_sub.subscriber_ids()


# ------------------------------------------------------------- session snapshot


def _status_trail(frames: list[dict]) -> list[str]:
    view = None
    trail = []
    for frame in frames:
        event = frame.get("event") or {}
        if event.get("kind") != "ConversationStateUpdateEvent":
            continue
        if event["key"] == "execution_status":
            view = event["value"]
        elif event["key"] == "full_state":
            view = event["value"]["execution_status"]
        trail.append(view)
    return trail


@pytest.mark.asyncio
async def test_session_snapshot_never_follows_newer_state(svc, monkeypatch):
    """The status changes after the connect snapshot is taken and before the
    socket reads its mark. The client must end on the current status."""
    await _emit(svc, _msg("E0"))
    real = svc._create_initial_state_sync
    snapped = asyncio.Event()
    loop = asyncio.get_running_loop()
    resume = asyncio.Event()

    def snap_then_hold():
        result = real()
        loop.call_soon_threadsafe(snapped.set)
        asyncio.run_coroutine_threadsafe(resume.wait(), loop).result(5)
        return result

    monkeypatch.setattr(svc, "_create_initial_state_sync", snap_then_hold)
    ws = _FakeWS()
    task = asyncio.create_task(_session(ws, svc, after_seq=-1))
    await snapped.wait()

    def set_running():
        conversation = svc._conversation
        assert conversation is not None
        with conversation._state:
            conversation._state.execution_status = ConversationExecutionStatus.RUNNING

    await asyncio.to_thread(set_running)
    wrapper = svc._callback_wrapper
    assert wrapper is not None
    await asyncio.to_thread(wrapper.wait_for_pending, 5)
    resume.set()
    await _wait_until(lambda: any(f.get("type") == "transient" for f in ws.sent))
    await _finish(ws, task)
    trail = _status_trail(ws.sent)
    assert trail and trail[-1] == "running", trail
