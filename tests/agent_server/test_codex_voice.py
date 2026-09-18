import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.codex_voice import (
    CodexRelay,
    CodexVoiceManager,
    RelayError,
    spoken_excerpt,
)
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import ConversationInfo
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.workspace import LocalWorkspace


_SERVER = r"""
import json, sys
options = json.loads(sys.argv[1])
results = []
requests = []
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    params = message.get("params", {})
    if method is None:
        results.append(message)
        continue
    if "id" not in message:
        continue
    requests.append(message)
    result = {}
    if method == "initialize":
        version = options.get("version", "0.154.0")
        result = {"userAgent": "openhands_insider_voice/" + version + " fixture"}
    elif method == "config/read":
        result = {"config": {"mcp_servers": options.get("mcp_servers", {})}}
    elif method == "account/read":
        signed_in = options.get("signed_in", True)
        result = {"account": {"type": "chatgpt"} if signed_in else None}
    elif method == "thread/start":
        result = {"thread": {"id": "bound-thread"}}
    elif method == "thread/realtime/start":
        send({"method": "thread/realtime/sdp",
              "params": {"threadId": "bound-thread", "sdp": "v=0\r\n"}})
    elif method == "thread/realtime/stop" and options.get("stall_stop"):
        continue
    elif method == "fixture/call":
        send({"id": params.pop("rpcId", 100),
              "method": params.pop("method", "item/tool/call"), "params": params})
    elif method == "fixture/notify":
        send(params)
    elif method == "fixture/results":
        result = {"results": results, "requests": requests}
    send({"id": message["id"], "result": result})
"""


@pytest.fixture
def protocol(tmp_path, monkeypatch):
    script = tmp_path / "app_server.py"
    script.write_text(_SERVER)
    options = {}
    monkeypatch.setattr(
        "openhands.agent_server.codex_voice._command",
        lambda home: ([sys.executable, str(script), json.dumps(options)], {}),
    )
    return options


@pytest.fixture
def relay_events(tmp_path):
    agent = Agent(llm=LLM(model="gpt-4o-mini", usage_id="relay-test"), tools=[])
    state = ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        persistence_dir=str(tmp_path / "conversation"),
    )
    events = AsyncMock(spec=EventService)
    events.get_state.return_value = state
    events.wait_for_run_completion.return_value = ConversationExecutionStatus.FINISHED

    async def send(message, run):
        assert run is True
        state.events.append(MessageEvent(llm_message=message, source="user"))
        state.events.append(
            ActionEvent(
                action=FinishAction(message="Saved coral."),
                thought=[],
                tool_name="finish",
                tool_call_id="finish-relay",
                tool_call=MessageToolCall(
                    id="finish-relay",
                    name="finish",
                    arguments='{"message":"Saved coral."}',
                    origin="completion",
                ),
                llm_response_id="relay-response",
            )
        )

    events.send_message.side_effect = send
    return events, state


async def _results(relay, count):
    async with asyncio.timeout(5):
        while True:
            result = await relay.rpc("fixture/results", {})
            if len(result["results"]) >= count:
                return result
            await asyncio.sleep(0.01)


def _call(**kwargs):
    return {
        "threadId": "bound-thread",
        "turnId": "turn-1",
        "callId": "tool-1",
        "namespace": None,
        "tool": "send_to_insider",
        "arguments": {"request": "Remember coral."},
        **kwargs,
    }


@pytest.mark.parametrize("answer", ["猫 remembers coral. 🐾", "Short answer."])
def test_spoken_excerpt_preserves_short_answer(answer):
    assert spoken_excerpt(answer) == answer


def test_spoken_excerpt_has_unicode_byte_limit_and_word_boundary():
    answer = "Opening answer. " + "猫🐾 explanation " * 1000 + "The ending."
    spoken = spoken_excerpt(answer)
    notice = "\n\nThe full answer is saved in this conversation."
    assert spoken.startswith("Opening answer. ")
    assert len(spoken.encode("utf-8")) <= 2400
    assert spoken.endswith(notice)
    prefix = spoken.removesuffix(notice)
    assert answer.startswith(prefix)
    assert answer[len(prefix)].isspace()
    assert "\ufffd" not in spoken


async def test_long_answer_keeps_saved_record_and_speaks_opening(
    protocol, relay_events, tmp_path
):
    events, state = relay_events
    full_answer = "Opening answer. " + "猫🐾 explanation " * 1500 + "The ending."

    async def send(message, run):
        state.events.append(MessageEvent(llm_message=message, source="user"))
        state.events.append(
            MessageEvent(
                llm_message=Message(
                    role="assistant", content=[TextContent(text=full_answer)]
                ),
                source="agent",
            )
        )

    events.send_message.side_effect = send
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/call", _call())
        result = await _results(relay, 1)
        returned = result["results"][0]["result"]["contentItems"][0]["text"]
        assert returned == full_answer[:12000]
        speech = next(
            x["params"]["text"]
            for x in result["requests"]
            if x["method"] == "thread/realtime/appendSpeech"
        )
        assert speech == spoken_excerpt(returned)
        saved = list(state.events)[-1]
        assert isinstance(saved, MessageEvent)
        content = saved.llm_message.content[0]
        assert isinstance(content, TextContent)
        assert content.text == full_answer
    finally:
        await relay.close()


async def test_real_stdio_replay_binding_and_context(protocol, relay_events, tmp_path):
    events, state = relay_events
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        assert await relay.start("v=0", "x" * 14000 + " saved coral") == "v=0\r\n"
        await relay.rpc("fixture/call", _call())
        first = await _results(relay, 1)
        await relay.rpc("fixture/call", _call(rpcId=101))
        await relay.rpc("fixture/call", _call(rpcId=102, callId="tool-2"))
        await relay.rpc(
            "fixture/call", _call(rpcId=103, turnId="turn-2", threadId="other")
        )
        result = await _results(relay, 4)
        assert events.send_message.await_count == 1
        assert result["results"][0]["result"] == result["results"][1]["result"]
        assert (
            first["results"][0]["result"]["contentItems"][0]["text"] == "Saved coral."
        )
        assert result["results"][2]["result"]["success"] is False
        assert result["results"][3]["result"]["success"] is False
        await relay.rpc(
            "fixture/notify",
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "bound-thread",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            },
        )
        assert relay.status.status == "listening"
        assert relay.status.error is None
        starts = [x for x in result["requests"] if x["method"] == "thread/start"]
        assert starts[0]["params"]["ephemeral"] is True
        assert starts[0]["params"]["environments"] == []
        realtime = next(
            x for x in result["requests"] if x["method"] == "thread/realtime/start"
        )
        assert len(realtime["params"]["initialItems"][0]["text"]) < 12100
        assert realtime["params"]["initialItems"][0]["role"] == "user"
        assert realtime["params"]["initialItems"][-1]["role"] == "developer"
        assert (
            realtime["params"]["initialItems"][-1]["text"]
            == realtime["params"]["prompt"]
        )
        assert "call send_to_insider" in realtime["params"]["realtimeStartInstructions"]
        assert realtime["params"]["clientManagedHandoffs"] is True
        assert (
            len(
                [
                    x
                    for x in result["requests"]
                    if x["method"] == "thread/realtime/appendSpeech"
                ]
            )
            == 1
        )
    finally:
        await relay.close()
    assert relay.process is not None and relay.process.returncode is not None
    assert not Path(relay.workspace.name).exists()


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "exec_command"},
        {"arguments": {"request": "hello", "conversation_id": "other"}},
        {"arguments": {"request": " "}},
        {"namespace": "other"},
        {"method": "item/commandExecution/requestApproval"},
    ],
)
async def test_protocol_rejects_unlisted_and_invalid_tools(
    protocol, relay_events, tmp_path, change
):
    events, state = relay_events
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/call", _call(**change))
        result = (await _results(relay, 1))["results"][0]
        assert "error" in result or result["result"]["success"] is False
        events.send_message.assert_not_awaited()
    finally:
        await relay.close()


@pytest.mark.parametrize("status", ["busy", "waiting_for_confirmation", "error"])
async def test_busy_or_approval_never_appends(protocol, relay_events, tmp_path, status):
    events, state = relay_events
    if status == "busy":
        events.wait_for_run_completion.side_effect = TimeoutError
    else:
        events.wait_for_run_completion.return_value = ConversationExecutionStatus(
            status
        )
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/call", _call())
        result = await _results(relay, 1)
        assert result["results"][0]["result"]["success"] is False
        events.send_message.assert_not_awaited()
    finally:
        await relay.close()


async def test_uncertain_append_result_is_cached(protocol, relay_events, tmp_path):
    events, state = relay_events
    events.send_message.side_effect = RuntimeError("private provider detail")
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/call", _call())
        await _results(relay, 1)
        await relay.rpc("fixture/call", _call(rpcId=101))
        result = await _results(relay, 2)
        assert events.send_message.await_count == 1
        assert "private provider detail" not in json.dumps(result["results"])
        assert "uncertain" in json.dumps(result["results"])
    finally:
        await relay.close()


async def test_hangup_cannot_split_accepted_append_and_run(
    protocol, relay_events, tmp_path
):
    events, state = relay_events
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def send(message, run):
        entered.set()
        await release.wait()
        completed.set()

    events.send_message.side_effect = send
    relay = CodexRelay(state.id, events, tmp_path / "home")
    await relay.start("v=0", "")
    await relay.rpc("fixture/call", _call())
    await asyncio.wait_for(entered.wait(), 3)
    await relay.close()
    assert not completed.is_set()
    release.set()
    await asyncio.wait_for(completed.wait(), 3)
    assert events.send_message.await_count == 1


async def test_cancelled_close_still_stops_child_and_repeated_close_joins(
    protocol, relay_events, tmp_path
):
    protocol["stall_stop"] = True
    events, state = relay_events
    relay = CodexRelay(state.id, events, tmp_path / "home")
    await relay.start("v=0", "")
    caller = asyncio.create_task(relay.close())
    async with asyncio.timeout(2):
        while True:
            observed = await relay.rpc("fixture/results", {})
            if any(x["method"] == "thread/realtime/stop" for x in observed["requests"]):
                break
            await asyncio.sleep(0.01)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    joined = asyncio.create_task(relay.close())
    await asyncio.sleep(0)
    assert not joined.done()
    await asyncio.wait_for(joined, 5)
    assert relay.process is not None and relay.process.returncode is not None
    assert not Path(relay.workspace.name).exists()
    events.send_message.assert_not_awaited()


@pytest.mark.parametrize("status", ["failed", "completed"])
async def test_unsaved_codex_turn_stops_voice_without_saving_request(
    relay_events, tmp_path, status
):
    events, state = relay_events
    relay = CodexRelay(state.id, events, tmp_path / "home")
    relay.thread_id = "bound-thread"
    try:
        relay._notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "bound-thread",
                    "turn": {"id": "unsaved-turn", "status": status},
                },
            }
        )
        relay._notification(
            {
                "method": "thread/realtime/transcript/done",
                "params": {
                    "threadId": "bound-thread",
                    "role": "assistant",
                    "text": "An answer that was never saved",
                },
            }
        )
        assert relay.status.status == "error"
        assert relay.status.error is not None
        assert relay.status.transcripts == []
        events.send_message.assert_not_awaited()
    finally:
        await relay.close()


@pytest.mark.parametrize("terminal", ["closed", "error"])
async def test_terminal_transport_ignores_late_transcript_and_work_completion(
    protocol, relay_events, tmp_path, terminal
):
    events, state = relay_events
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait(timeout):
        if timeout:
            entered.set()
            await release.wait()
        return ConversationExecutionStatus.FINISHED

    events.wait_for_run_completion.side_effect = wait
    relay = CodexRelay(state.id, events, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/call", _call())
        await asyncio.wait_for(entered.wait(), 3)
        for method, params in [
            (f"thread/realtime/{terminal}", {}),
            (
                "thread/realtime/transcript/delta",
                {"role": "assistant", "delta": "late"},
            ),
            ("thread/realtime/transcript/done", {"role": "assistant", "text": "late"}),
        ]:
            await relay.rpc(
                "fixture/notify",
                {
                    "method": method,
                    "params": {
                        "threadId": "bound-thread",
                        **params,
                    },
                },
            )
        await relay.rpc(
            "fixture/call", _call(rpcId=101, turnId="turn-2", callId="tool-2")
        )
        release.set()
        result = await _results(relay, 2)
        assert relay.status.status == terminal
        assert relay.status.transcripts == []
        assert not any(
            x["method"] == "thread/realtime/appendSpeech" for x in result["requests"]
        )
        assert events.send_message.await_count == 1
        assert any(x["result"]["success"] is False for x in result["results"])
        saved = list(state.events)[-1]
        assert isinstance(saved, ActionEvent)
        assert isinstance(saved.action, FinishAction)
        assert saved.action.message == "Saved coral."
    finally:
        release.set()
        await relay.close()


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"signed_in": False}, "codex_not_signed_in"),
        ({"version": "99.0.0"}, "codex_unavailable"),
        ({"mcp_servers": {"inherited": {"command": "never-run"}}}, "codex_unavailable"),
    ],
)
async def test_availability_fails_closed(
    protocol, relay_events, tmp_path, options, reason
):
    protocol.update(options)
    events, state = relay_events
    manager = CodexVoiceManager(tmp_path / "home")
    assert await manager.availability(state.id, events) == reason
    with pytest.raises(RelayError):
        await manager.start(state.id, events, "v=0", "")
    assert manager.calls == {}
    events.send_message.assert_not_awaited()


async def test_codex_routes_are_authenticated_scoped_and_backward_compatible(
    protocol, relay_events, tmp_path
):
    events, state = relay_events
    config = Config(
        voice_provider="codex",
        codex_voice_home=tmp_path / "home",
        session_api_keys=["fixture"],
        secret_key=SecretStr("fixture"),
    )
    app = create_app(config)
    service = AsyncMock(spec=ConversationService)
    service.get_event_service.return_value = events
    service.get_conversation.return_value = ConversationInfo(
        id=state.id,
        agent=state.agent,
        workspace=state.workspace,
        tags={"smolpaws": "insider"},
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://fixture",
        headers={"X-Session-API-Key": "fixture"},
    ) as client:
        path = f"/api/conversations/{state.id}/voice"
        available = (await client.get(path)).json()
        assert available["available"] is True
        assert available["provider"] == "codex" and available["delegation"] == "server"
        answer = (await client.post(path + "/realtime", json={"sdp": "v=0"})).json()
        assert answer["sdp"] == "v=0\r\n"
        scoped = f"{path}/realtime/{answer['call_id']}"
        assert (await client.get(scoped)).json()["status"] == "listening"
        assert (
            await client.get(scoped.replace(str(state.id), str(uuid4())))
        ).status_code == 404
        assert (
            await client.delete(scoped.replace(str(state.id), str(uuid4())))
        ).status_code == 404
        client.headers.pop("X-Session-API-Key")
        assert (await client.get(scoped)).status_code == 401
        client.headers["X-Session-API-Key"] = "fixture"
        assert (await client.delete(scoped)).status_code == 200
        assert (await client.get(scoped)).status_code == 404
    await app.state.codex_voice.close()
