import asyncio
import json
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import ConversationInfo, StoredConversation
from openhands.agent_server.persistence import get_secrets_store
from openhands.sdk import LLM, Agent, Conversation, Message, TextContent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import ActionEvent, Condensation, MessageEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def voice_client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = Config(
        session_api_keys=["canvas-fixture"], secret_key=SecretStr("fixture-cipher")
    )
    app = create_app(config)
    agent = Agent(llm=LLM(model="gpt-4o-mini", usage_id="voice-test"), tools=[])
    state = ConversationState.create(
        id=uuid4(),
        agent=agent,
        workspace=LocalWorkspace(working_dir=str(tmp_path)),
        persistence_dir=str(tmp_path / "conversation"),
    )
    info = ConversationInfo(
        id=state.id,
        agent=agent,
        workspace=state.workspace,
        tags={"smolpaws": "insider", "insiderrole": "controller"},
    )
    service = AsyncMock(spec=ConversationService)
    service.get_conversation.return_value = info
    events = AsyncMock(spec=EventService)
    events.get_state.return_value = state
    events.wait_for_run_completion.return_value = state.execution_status
    service.get_event_service.return_value = events
    app.dependency_overrides[get_conversation_service] = lambda: service
    client = TestClient(
        app,
        headers={"X-Session-API-Key": "canvas-fixture"},
        raise_server_exceptions=False,
    )
    yield client, state, service, config
    client.close()


@pytest.fixture
def upstream(monkeypatch):
    requests: list[httpx.Request] = []
    responses: list[httpx.Response | Exception] = []
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.url.host == "api.openai.com"
        requests.append(request)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(
        "openhands.agent_server.voice_router.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs),
    )
    return requests, responses


def _session(request: httpx.Request) -> dict:
    mime = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.content
    )
    for part in mime.iter_parts():
        if part.get_param("name", header="content-disposition") == "session":
            payload = part.get_payload(decode=True)
            assert isinstance(payload, bytes)
            return json.loads(payload)
    raise AssertionError("Missing session multipart field")


@pytest.mark.parametrize(
    "method,suffix",
    [("GET", ""), ("POST", "/realtime"), ("DELETE", "/realtime/rtc_fixture")],
)
def test_voice_requires_canvas_auth(voice_client, method, suffix):
    client, state, _, _ = voice_client
    client.headers.pop("X-Session-API-Key")
    response = client.request(method, f"/api/conversations/{state.id}/voice{suffix}")
    assert response.status_code == 401


def test_voice_rejects_wrong_controller_and_missing_conversation(voice_client):
    client, state, service, _ = voice_client
    service.get_conversation.return_value = (
        service.get_conversation.return_value.model_copy(update={"tags": {}})
    )
    path = f"/api/conversations/{state.id}/voice"
    assert client.get(path).status_code == 409
    assert client.post(f"{path}/realtime", json={"sdp": "v=0"}).status_code == 409
    service.get_conversation.return_value = None
    assert client.get(path).status_code == 404


def test_voice_missing_key_does_not_start_provider_call(voice_client, upstream):
    client, state, _, _ = voice_client
    requests, _ = upstream
    path = f"/api/conversations/{state.id}/voice"
    response = client.get(path)
    assert response.json() == {
        "available": False,
        "run_active": False,
        "execution_status": "idle",
        "model": "gpt-realtime-2.1",
        "provider": "openai",
        "delegation": "client",
        "reason": "missing_openai_api_key",
    }
    assert response.headers["cache-control"] == "no-store"
    assert client.post(f"{path}/realtime", json={"sdp": "v=0"}).status_code == 409
    assert requests == []


@pytest.mark.parametrize("settled_status", ["finished", "waiting_for_confirmation"])
async def test_voice_waits_for_real_run_settlement(
    voice_client, tmp_path, settled_status
):
    client, state, service, _ = voice_client
    conversation = Conversation(
        agent=state.agent,
        workspace=state.workspace,
        persistence_dir=str(tmp_path / "live-run"),
    )
    try:
        assert isinstance(conversation, LocalConversation)
        events = EventService(
            stored=StoredConversation(id=conversation.id, workspace=state.workspace),
            conversations_dir=tmp_path,
        )
        events._conversation = conversation
        release_run = asyncio.Event()
        pending_run = asyncio.create_task(release_run.wait())
        events._run_task = pending_run
        service.get_event_service.return_value = events
        service.get_conversation.return_value = (
            service.get_conversation.return_value.model_copy(
                update={
                    "id": conversation.id,
                    "execution_status": ConversationExecutionStatus.FINISHED,
                }
            )
        )
        with conversation._state:
            conversation._state.execution_status = ConversationExecutionStatus.FINISHED
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://voice-fixture",
                headers={"X-Session-API-Key": "canvas-fixture"},
            ) as http:
                path = f"/api/conversations/{conversation.id}/voice"
                active = await http.get(path)
                assert active.status_code == 200
                assert active.json()["run_active"] is True
                assert not pending_run.done()
                with conversation._state:
                    conversation._state.execution_status = ConversationExecutionStatus(
                        settled_status
                    )
                release_run.set()
                await pending_run
                settled = await http.get(path)
                assert settled.status_code == 200
                assert settled.json()["run_active"] is False
                assert settled.json()["execution_status"] == settled_status
        finally:
            release_run.set()
            await pending_run
    finally:
        conversation.close()


def test_voice_resumes_legacy_insider_but_rejects_workers(voice_client):
    client, state, service, _ = voice_client
    info = service.get_conversation.return_value
    path = f"/api/conversations/{state.id}/voice"
    service.get_conversation.return_value = info.model_copy(
        update={"tags": {"smolpaws": "insider"}}
    )
    assert client.get(path).status_code == 200
    for role in (None, "controller", "worker"):
        tags = {"smolpaws": "insider"}
        if role:
            tags["insiderrole"] = role
        service.get_conversation.return_value = info.model_copy(
            update={"tags": tags, "parent_conversation_id": uuid4()}
        )
        assert client.get(path).status_code == 409
    service.get_conversation.return_value = info.model_copy(
        update={"tags": {"smolpaws": "insider", "insiderrole": "worker"}}
    )
    assert client.get(path).status_code == 409


def test_voice_restores_condensed_context_and_scopes_hangup(voice_client, upstream):
    client, state, _, config = voice_client
    requests, responses = upstream
    get_secrets_store(config).set_secret("OPENAI_API_KEY", "fixture-openai-key")
    forgotten = MessageEvent(
        source="user",
        llm_message=Message(
            role="user", content=[TextContent(text="outdated private request")]
        ),
    )
    state.events.append(forgotten)
    state.events.append(
        Condensation(
            forgotten_event_ids={forgotten.id},
            summary="The project is a cat dashboard.",
            summary_offset=0,
            llm_response_id="condense-fixture",
        )
    )
    state.events.append(
        MessageEvent(
            source="agent",
            llm_message=Message(
                role="assistant",
                content=[TextContent(text="The board is ready.")],
                reasoning_content="hidden reasoning",
            ),
        )
    )
    state.events.append(
        ActionEvent(
            source="agent",
            thought=[TextContent(text="hidden tool thought")],
            action=FinishAction(message="Your saved result"),
            tool_name="finish",
            tool_call_id="call-fixture",
            tool_call=MessageToolCall(
                id="call-fixture",
                name="finish",
                arguments='{"message":"Your saved result"}',
                origin="completion",
            ),
            llm_response_id="finish-fixture",
        )
    )
    path = f"/api/conversations/{state.id}/voice"
    assert client.get(path).json()["available"] is True
    responses.append(
        httpx.Response(
            201,
            text="v=0\r\nanswer",
            headers={"location": "/v1/realtime/calls/rtc_fixture"},
        )
    )
    response = client.post(f"{path}/realtime", json={"sdp": "v=0\r\noffer"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "sdp": "v=0\r\nanswer",
        "call_id": "rtc_fixture",
        "model": "gpt-realtime-2.1",
        "provider": "openai",
        "delegation": "client",
    }
    assert "fixture-openai-key" not in response.text
    assert response.headers["cache-control"] == "no-store"
    session = _session(requests[0])
    assert session["model"] == "gpt-realtime-2.1"
    assert session["tools"][0]["name"] == "send_to_insider"
    prompt = session["instructions"]
    assert str(state.id) in prompt
    assert "The project is a cat dashboard." in prompt
    assert "The board is ready." in prompt and "Your saved result" in prompt
    assert "outdated private request" not in prompt
    assert "hidden reasoning" not in prompt and "hidden tool thought" not in prompt
    state.events.append(
        MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="x" * 20000 + "fresh ending")]
            ),
        )
    )
    responses.append(httpx.Response(201, text="v=0\r\nanswer"))
    assert client.post(f"{path}/realtime", json={"sdp": "v=0"}).status_code == 200
    saved_context = (
        _session(requests[-1])["instructions"]
        .split("<saved_context>\n", 1)[1]
        .removesuffix("\n</saved_context>")
    )
    assert len(saved_context) <= 12000
    assert saved_context.endswith("fresh ending")
    assert (
        client.delete(
            f"/api/conversations/{uuid4()}/voice/realtime/rtc_fixture"
        ).status_code
        == 404
    )
    responses.append(httpx.Response(200))
    assert client.delete(f"{path}/realtime/rtc_fixture").json() == {"success": True}
    assert requests[-1].url.path == "/v1/realtime/calls/rtc_fixture/hangup"
    assert client.delete(f"{path}/realtime/rtc_fixture").status_code == 404


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(401, text="fixture-openai-key rejected"),
        httpx.ReadTimeout("fixture-openai-key"),
    ],
)
def test_voice_upstream_failures_do_not_expose_credentials(
    voice_client, upstream, monkeypatch, failure
):
    client, state, _, _ = voice_client
    _, responses = upstream
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-openai-key")
    responses.append(failure)
    response = client.post(
        f"/api/conversations/{state.id}/voice/realtime", json={"sdp": "v=0"}
    )
    assert response.status_code == 502
    assert "fixture-openai-key" not in response.text
