"""Event sockets against a real EventService: replay races and teardown."""

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
from openhands.agent_server.server_context import build_server_context
from openhands.sdk import Message, TextContent
from openhands.sdk.agent import Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent, StreamingDeltaEvent
from openhands.sdk.llm import LLM
from openhands.sdk.workspace import LocalWorkspace


def _msg(text: str) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple | None = None
        self.disconnect = asyncio.Event()
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.headers: dict = {}
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                server_context=build_server_context(Config(session_api_keys=[]))
            )
        )

    async def accept(self):
        pass

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)
        self.application_state = WebSocketState.DISCONNECTED

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
            pass
    return out


async def _wait_until(predicate):
    async with asyncio.timeout(5):
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

    conversation_service = SimpleNamespace(get_event_service=get_event_service)
    for module in (sockets, session_socket):
        monkeypatch.setattr(
            module, "get_conversation_service", lambda _ws: conversation_service
        )
    yield service
    if service.is_open():
        await service.close()


def _emit_sync(svc: EventService, event) -> None:
    with svc.get_conversation()._state:
        svc.get_conversation()._on_event(event)


async def _settle(svc: EventService) -> None:
    await asyncio.to_thread(svc._callback_wrapper.wait_for_pending, 5)  # type: ignore[union-attr]


async def _emit(svc: EventService, event) -> None:
    await asyncio.to_thread(_emit_sync, svc, event)
    await _settle(svc)


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


def _client_status(frames: list[dict], session: bool = False) -> str | None:
    status = None
    for frame in frames:
        event = (frame.get("event") if session else frame) or {}
        if event.get("kind") != "ConversationStateUpdateEvent":
            continue
        if event["key"] == "execution_status":
            status = event["value"]
        elif event["key"] == "full_state":
            status = event["value"]["execution_status"]
    return status


def _set_status(svc: EventService, status: ConversationExecutionStatus) -> None:
    with svc.get_conversation()._state as state:
        state.execution_status = status


async def _finish(ws, task):
    await asyncio.sleep(0.2)
    ws.disconnect.set()
    await task


@pytest.mark.asyncio
async def test_legacy_replay_with_queued_publish_is_exactly_once_in_order(svc):
    await _emit(svc, _msg("E0"))
    ws = _FakeWS()
    _emit_sync(svc, _msg("E1"))
    task = asyncio.create_task(_legacy(ws, svc, resend_mode="all"))
    await _wait_until(lambda: "E1" in _texts(ws.sent))
    await _finish(ws, task)
    assert _texts(ws.sent) == ["E0", "E1"]


async def _held_legacy_replay(svc, monkeypatch, ws):
    gate, in_replay = asyncio.Event(), asyncio.Event()
    search_events = svc.search_events

    async def held_search(*args, **kwargs):
        in_replay.set()
        await gate.wait()
        return await search_events(*args, **kwargs)

    monkeypatch.setattr(svc, "search_events", held_search)
    task = asyncio.create_task(_legacy(ws, svc, resend_mode="all"))
    await in_replay.wait()
    return gate, task


@pytest.mark.asyncio
async def test_legacy_event_during_replay_follows_history_once(svc, monkeypatch):
    for i in range(3):
        await _emit(svc, _msg(f"E{i}"))
    ws = _FakeWS()
    gate, task = await _held_legacy_replay(svc, monkeypatch, ws)
    await _emit(svc, _msg("E3"))
    gate.set()
    await _wait_until(lambda: "E3" in _texts(ws.sent))
    await _finish(ws, task)
    assert _texts(ws.sent) == ["E0", "E1", "E2", "E3"]
    assert ws.sent[0]["kind"] == "ConversationStateUpdateEvent"


@pytest.mark.asyncio
async def test_legacy_snapshot_during_replay_does_not_override_newer_state(
    svc, monkeypatch
):
    ws = _FakeWS()
    gate, task = await _held_legacy_replay(svc, monkeypatch, ws)
    await asyncio.to_thread(_set_status, svc, ConversationExecutionStatus.RUNNING)
    await svc._publish_state_update()
    await asyncio.to_thread(_set_status, svc, ConversationExecutionStatus.IDLE)
    await _settle(svc)
    gate.set()
    await _finish(ws, task)
    assert _client_status(ws.sent) == "idle"


@pytest.mark.asyncio
async def test_legacy_delta_during_replay_precedes_its_message(svc, monkeypatch):
    ws = _FakeWS()
    gate, task = await _held_legacy_replay(svc, monkeypatch, ws)
    await svc._pub_sub(StreamingDeltaEvent(content="done"))
    await _emit(svc, _msg("done"))
    gate.set()
    await _finish(ws, task)
    kinds = [f["kind"] for f in ws.sent]
    assert [k for k in kinds if k in ("StreamingDeltaEvent", "MessageEvent")] == [
        "StreamingDeltaEvent",
        "MessageEvent",
    ]


@pytest.mark.asyncio
async def test_failed_subscribe_releases_its_slot(svc, monkeypatch):
    baseline = svc._pub_sub.subscriber_ids()

    def boom():
        raise ValueError("inactive_service")

    monkeypatch.setattr(svc, "_create_initial_state_sync", boom)
    ws = _FakeWS()
    await _session(ws, svc)
    assert ws.closed == (1011, "subscribe_failed")
    with pytest.raises(ValueError):
        await _legacy(_FakeWS(), svc)
    assert svc._pub_sub.subscriber_ids() == baseline


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
    pub_sub = PubSub[str]()
    gate = asyncio.Event()
    pub_sub.subscribe(_Sub(gate=gate))
    closing = asyncio.create_task(pub_sub.close())
    await asyncio.sleep(0)
    late = _Sub()
    pub_sub.subscribe(late)
    gate.set()
    await closing
    assert late.closed and not pub_sub.subscriber_ids()


@pytest.mark.asyncio
async def test_pubsub_close_survives_a_failing_subscriber():
    pub_sub = PubSub[str]()
    ok = _Sub()
    pub_sub.subscribe(_Sub(raise_on_close=True))
    pub_sub.subscribe(ok)
    await pub_sub.close()
    assert ok.closed and not pub_sub.subscriber_ids()


async def _held_session_snapshot(svc, monkeypatch, ws):
    loop = asyncio.get_running_loop()
    snapped, resume = asyncio.Event(), asyncio.Event()
    create_initial_state = svc._create_initial_state_sync

    def snap_then_hold():
        result = create_initial_state()
        loop.call_soon_threadsafe(snapped.set)
        asyncio.run_coroutine_threadsafe(resume.wait(), loop).result(5)
        return result

    monkeypatch.setattr(svc, "_create_initial_state_sync", snap_then_hold)
    task = asyncio.create_task(_session(ws, svc, after_seq=-1))
    await snapped.wait()
    return resume, task


@pytest.mark.asyncio
async def test_session_snapshot_never_follows_newer_state(svc, monkeypatch):
    await _emit(svc, _msg("E0"))
    ws = _FakeWS()
    resume, task = await _held_session_snapshot(svc, monkeypatch, ws)
    await asyncio.to_thread(_set_status, svc, ConversationExecutionStatus.RUNNING)
    await _settle(svc)
    resume.set()
    await _wait_until(lambda: any(f.get("type") == "transient" for f in ws.sent))
    await _finish(ws, task)
    assert _client_status(ws.sent, session=True) == "running"


@pytest.mark.asyncio
async def test_session_newer_snapshot_follows_connect_snapshot(svc, monkeypatch):
    ws = _FakeWS()
    resume, task = await _held_session_snapshot(svc, monkeypatch, ws)

    def activate_skill():
        with svc.get_conversation()._state as state:
            state.activated_knowledge_skills.append("python_tips")

    await asyncio.to_thread(activate_skill)
    await svc._publish_state_update()
    resume.set()
    await _wait_until(lambda: sum(f.get("type") == "transient" for f in ws.sent) >= 2)
    await _finish(ws, task)
    snapshots = [
        f["event"]["value"]
        for f in ws.sent
        if f.get("type") == "transient" and f["event"].get("key") == "full_state"
    ]
    assert snapshots[-1]["activated_knowledge_skills"] == ["python_tips"]
