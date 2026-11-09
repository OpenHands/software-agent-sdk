"""Docker gateway requests must run in their conversation container."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from openhands.agent_server import event_router
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.hybrid import HybridConversationRegistry
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.event_router import run_gateway_message
from openhands.agent_server.event_service import ConversationRunLimitExceeded
from openhands.agent_server.openai import service as gateway
from openhands.agent_server.persistence import ServerStores
from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.conversation.message_request import SendMessageRequest
from openhands.sdk.conversation.request import StartConversationRequest
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm.message import TextContent
from openhands.sdk.llm.utils.metrics import Metrics, TokenUsage
from openhands.sdk.workspace import LocalWorkspace


def _setup(tmp_path):
    config = Config(
        conversation_runtime="docker",
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "outer-workspace",
        secret_key=SecretStr("outer-key"),
    )
    registry = DockerConversationRegistry(config, ServerStores.from_config(config))
    outer_service = AsyncMock()
    request = StartConversationRequest(
        agent_profile_id=uuid4(),
        workspace=LocalWorkspace(working_dir=str(config.workspace_path)),
    )
    return config, registry, outer_service, request


def _mock_container(monkeypatch, *, host="http://inner"):
    container = SimpleNamespace(host=host, api_key="inner-key")
    mock = AsyncMock(return_value=container)
    monkeypatch.setattr(gateway, "_container", mock)
    return mock


async def _run_gateway(
    config,
    registry,
    outer_service,
    start_request,
    text,
    *,
    conversation_id=None,
):
    return await gateway._run_agent(
        start_request=start_request,
        user_content=[TextContent(text=text)],
        conversation_service=outer_service,
        reusable_conversation_id=conversation_id,
        config=config,
        registry=registry,
    )


def _gateway_event_service():
    event_service = AsyncMock()
    event_service._run_task = None
    event_service._gateway_run_lock = asyncio.Lock()
    return event_service


def _inner_client(
    monkeypatch,
    seen,
    *,
    status_code=200,
    error_detail="Inner run failed",
    on_request=None,
):
    original_client = httpx.AsyncClient

    def handle(request):
        seen.append(request)
        if on_request is not None:
            on_request(request)
        if status_code != 200:
            return httpx.Response(status_code, json={"detail": error_detail})
        return httpx.Response(
            200,
            json={
                "status": "finished",
                "final_response": "Reply from the container",
                "usage": TokenUsage(prompt_tokens=10, completion_tokens=5).model_dump(
                    mode="json"
                ),
            },
        )

    monkeypatch.setattr(
        gateway,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: original_client(
                transport=httpx.MockTransport(handle), **kwargs
            ),
            HTTPError=httpx.HTTPError,
            TimeoutException=httpx.TimeoutException,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected_id", [False, True], ids=["generated-id", "caller-selected-id"]
)
async def test_h7m4_new_gateway_run_uses_isolated_docker_workspace(
    tmp_path, monkeypatch, selected_id
):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    started = []
    requests = []

    async def start(body, *args, **kwargs):
        started.append(body)
        return 200, {}, conversation_id

    monkeypatch.setattr(gateway, "start_docker_conversation", start)
    _mock_container(monkeypatch)
    _inner_client(monkeypatch, requests)

    if selected_id:
        start_request = start_request.model_copy(
            update={"conversation_id": conversation_id}
        )
    result = await _run_gateway(
        config,
        registry,
        outer_service,
        start_request,
        "Hello",
        conversation_id=conversation_id if selected_id else None,
    )

    assert result.conversation_id == conversation_id
    assert result.final_response == "Reply from the container"
    assert started[0]["workspace"]["working_dir"] == "/workspace"
    assert started[0]["initial_message"] is None
    if selected_id:
        assert started[0]["conversation_id"] == str(conversation_id)
    assert requests[0].url.path == (
        f"/api/conversations/{conversation_id}/events/_gateway/run"
    )
    assert requests[0].headers["X-Session-API-Key"] == "inner-key"
    assert json.loads(requests[0].content)["content"][0]["text"] == "Hello"
    outer_service.start_conversation.assert_not_awaited()
    outer_service.get_event_service.assert_not_awaited()
    assert outer_service.refresh_persisted_conversation.await_args_list == [
        call(conversation_id),
        call(conversation_id),
    ]
    assert not registry.has_attached_sessions(conversation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("hybrid", [False, True], ids=["docker", "hybrid"])
async def test_f55r_resume_gateway_run_stays_in_existing_docker_container(
    tmp_path, monkeypatch, hybrid
):
    config, registry, outer_service, start_request = _setup(tmp_path)
    if hybrid:
        config = config.model_copy(
            update={
                "conversation_runtime": "local",
                "conversation_runtime_selectable": True,
            }
        )
        registry = HybridConversationRegistry(config, ServerStores.from_config(config))
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    requests = []
    monkeypatch.setattr(
        gateway,
        "start_docker_conversation",
        AsyncMock(side_effect=AssertionError("resume must not start a conversation")),
    )
    _mock_container(monkeypatch)
    _inner_client(monkeypatch, requests)

    result = await _run_gateway(
        config,
        registry,
        outer_service,
        start_request,
        "Second turn",
        conversation_id=conversation_id,
    )

    assert result.conversation_id == conversation_id
    assert result.final_response == "Reply from the container"
    assert len(requests) == 1
    assert json.loads(requests[0].content)["content"][0]["text"] == "Second turn"
    outer_service.get_event_service.assert_not_awaited()
    outer_service.start_conversation.assert_not_awaited()
    assert not registry.has_attached_sessions(conversation_id)


@pytest.mark.asyncio
async def test_docker_gateway_ignores_environment_http_proxy(tmp_path, monkeypatch):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    requests = []

    class InnerHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers["X-Session-API-Key"]))
            body = json.dumps(
                {
                    "status": "finished",
                    "final_response": "Reply from the container",
                    "usage": None,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), InnerHandler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    _mock_container(monkeypatch, host=f"http://127.0.0.1:{server.server_port}")
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")

    try:
        result = await _run_gateway(
            config,
            registry,
            outer_service,
            start_request,
            "Second turn",
            conversation_id=conversation_id,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    assert result.final_response == "Reply from the container"
    assert requests == [
        (f"/api/conversations/{conversation_id}/events/_gateway/run", "inner-key")
    ]
    assert not registry.has_attached_sessions(conversation_id)


@pytest.mark.asyncio
async def test_docker_gateway_keeps_container_attached_until_inner_error(
    tmp_path, monkeypatch
):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    requests = []
    _mock_container(monkeypatch)

    def check_attached(request):
        assert registry.has_attached_sessions(conversation_id)

    _inner_client(
        monkeypatch,
        requests,
        status_code=503,
        on_request=check_attached,
    )

    with pytest.raises(HTTPException) as error:
        await _run_gateway(
            config,
            registry,
            outer_service,
            start_request,
            "Second turn",
            conversation_id=conversation_id,
        )

    assert error.value.status_code == 503
    assert len(requests) == 1
    assert not registry.has_attached_sessions(conversation_id)
    outer_service.get_event_service.assert_not_awaited()
    outer_service.refresh_persisted_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_gateway_reports_inner_timeout_as_gateway_timeout(
    tmp_path, monkeypatch
):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    _mock_container(monkeypatch)
    monkeypatch.setattr(
        gateway,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow"))
                ),
                **kwargs,
            ),
            HTTPError=httpx.HTTPError,
            TimeoutException=httpx.TimeoutException,
        ),
    )

    with pytest.raises(HTTPException) as error:
        await _run_gateway(
            config,
            registry,
            outer_service,
            start_request,
            "Second turn",
            conversation_id=conversation_id,
        )

    assert error.value.status_code == 504
    assert not registry.has_attached_sessions(conversation_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_conversation",
    [False, True],
    ids=["missing-route", "missing-conversation"],
)
async def test_docker_gateway_maps_inner_404(
    tmp_path, monkeypatch, missing_conversation
):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    registry.provisioning.create(conversation_id)
    _mock_container(monkeypatch)
    requests = []
    missing_detail = f"Conversation not found: {conversation_id}"
    _inner_client(
        monkeypatch,
        requests,
        status_code=404,
        error_detail=missing_detail if missing_conversation else "Inner run failed",
    )

    with pytest.raises(HTTPException) as error:
        await _run_gateway(
            config,
            registry,
            outer_service,
            start_request,
            "Second turn",
            conversation_id=conversation_id,
        )

    assert len(requests) == 1
    if missing_conversation:
        assert error.value.status_code == 404
        assert error.value.detail == missing_detail
    else:
        assert error.value.status_code == 502
        assert "gateway route" in error.value.detail


@pytest.mark.asyncio
async def test_docker_gateway_stops_after_docker_start_error(tmp_path, monkeypatch):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    start = AsyncMock(return_value=(503, {}, conversation_id))
    inner = AsyncMock()
    monkeypatch.setattr(gateway, "start_docker_conversation", start)
    monkeypatch.setattr(gateway, "_container", inner)

    with pytest.raises(HTTPException) as error:
        await _run_gateway(config, registry, outer_service, start_request, "Do not run")

    assert error.value.status_code == 503
    start.assert_awaited_once()
    inner.assert_not_awaited()
    outer_service.start_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_gateway_refuses_outer_launch_without_registry(tmp_path):
    config, _, outer_service, start_request = _setup(tmp_path)

    with pytest.raises(HTTPException) as error:
        await _run_gateway(
            config, None, outer_service, start_request, "Do not run on host"
        )

    assert error.value.status_code == 503
    outer_service.start_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_docker_gateway_rejects_id_owned_by_local_conversation(tmp_path):
    config, registry, outer_service, start_request = _setup(tmp_path)
    conversation_id = uuid4()
    conversation_dir = registry.conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True)
    conversation_dir.joinpath("meta.json").write_text("{}")

    with pytest.raises(HTTPException) as error:
        await _run_gateway(
            config,
            registry,
            outer_service,
            start_request,
            "Do not run on host",
            conversation_id=conversation_id,
        )

    assert error.value.status_code == 404
    outer_service.get_event_service.assert_not_awaited()
    outer_service.start_conversation.assert_not_awaited()


@pytest.mark.asyncio
async def test_hybrid_gateway_keeps_a_local_owned_conversation_local(tmp_path):
    config, _, outer_service, start_request = _setup(tmp_path)
    config = config.model_copy(update={"conversation_runtime_selectable": True})
    registry = HybridConversationRegistry(config, ServerStores.from_config(config))
    conversation_id = uuid4()
    conversation_dir = registry.conversation_dir(conversation_id)
    conversation_dir.mkdir(parents=True)
    conversation_dir.joinpath("meta.json").write_text("{}")
    event_service = _gateway_event_service()
    event_service.get_state.side_effect = [
        SimpleNamespace(
            events=[],
            stats=ConversationStats(),
            execution_status=ConversationExecutionStatus.FINISHED,
        ),
        SimpleNamespace(
            events=[1, 2],
            stats=ConversationStats(),
            execution_status=ConversationExecutionStatus.FINISHED,
        ),
        SimpleNamespace(
            events=[1, 2],
            stats=ConversationStats(),
            execution_status=ConversationExecutionStatus.FINISHED,
        ),
    ]
    event_service.wait_for_run_completion.return_value = (
        ConversationExecutionStatus.FINISHED
    )
    event_service.reserve_run_slot.return_value = None
    event_service.get_agent_final_response.return_value = "Local reply"
    outer_service.get_event_service.return_value = event_service

    result = await _run_gateway(
        config,
        registry,
        outer_service,
        start_request,
        "Local turn",
        conversation_id=conversation_id,
    )

    assert result.final_response == "Local reply"
    outer_service.get_event_service.assert_awaited_once_with(conversation_id)


@pytest.mark.asyncio
async def test_gateway_run_reserves_capacity_before_saving_message():
    calls = []
    slot = SimpleNamespace(release=lambda: calls.append("release"))
    event_service = _gateway_event_service()

    async def reserve():
        calls.append("reserve")
        return slot

    async def send(*args, **kwargs):
        calls.append("send")

    event_service.reserve_run_slot.side_effect = reserve
    event_service.send_message.side_effect = send
    event_service.wait_for_run_completion.return_value = (
        ConversationExecutionStatus.FINISHED
    )
    event_service.get_state.side_effect = [
        SimpleNamespace(events=[], stats=ConversationStats()),
        SimpleNamespace(events=[1, 2], stats=ConversationStats()),
    ]
    event_service.get_agent_final_response.return_value = "Finished"

    result = await run_gateway_message(
        SendMessageRequest(content=[TextContent(text="Hello")]), event_service
    )

    assert calls == ["reserve", "send", "release"]
    assert result["final_response"] == "Finished"


@pytest.mark.asyncio
async def test_gateway_run_does_not_save_message_when_capacity_is_full():
    event_service = _gateway_event_service()
    event_service.get_state.return_value = SimpleNamespace(
        events=[], stats=ConversationStats()
    )
    event_service.reserve_run_slot.side_effect = ConversationRunLimitExceeded("Full")

    with pytest.raises(ConversationRunLimitExceeded):
        await run_gateway_message(SendMessageRequest(), event_service)

    event_service.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_run_waits_for_events_after_its_own_message():
    event_service = _gateway_event_service()
    event_service.reserve_run_slot.return_value = None
    event_service.wait_for_run_completion.return_value = (
        ConversationExecutionStatus.FINISHED
    )
    event_service.get_state.side_effect = [
        SimpleNamespace(events=[1, 2], stats=ConversationStats()),
        SimpleNamespace(events=[1, 2, 3], stats=ConversationStats()),
        SimpleNamespace(events=[1, 2, 3, 4], stats=ConversationStats()),
    ]
    event_service.get_agent_final_response.return_value = "Current turn"

    result = await run_gateway_message(SendMessageRequest(), event_service)

    assert result["final_response"] == "Current turn"
    assert event_service.wait_for_run_completion.await_count == 2


@pytest.mark.asyncio
async def test_resumed_gateway_run_reports_only_this_turns_tokens():
    event_service = _gateway_event_service()
    event_service.reserve_run_slot.return_value = None
    event_service.wait_for_run_completion.return_value = (
        ConversationExecutionStatus.FINISHED
    )
    before = ConversationStats(
        usage_to_metrics={
            "default": Metrics(
                accumulated_token_usage=TokenUsage(
                    prompt_tokens=10, completion_tokens=5
                )
            )
        }
    )
    after = ConversationStats(
        usage_to_metrics={
            "default": Metrics(
                accumulated_token_usage=TokenUsage(
                    prompt_tokens=20, completion_tokens=10
                )
            )
        }
    )
    event_service.get_state.side_effect = [
        SimpleNamespace(events=[1, 2], stats=before),
        SimpleNamespace(events=[1, 2, 3, 4], stats=after),
    ]
    event_service.get_agent_final_response.return_value = "Second turn"

    result = await run_gateway_message(SendMessageRequest(), event_service)

    assert result["usage"]["prompt_tokens"] == 10
    assert result["usage"]["completion_tokens"] == 5


@pytest.mark.asyncio
async def test_gateway_turns_on_one_conversation_run_in_order():
    event_service = _gateway_event_service()
    event_service.reserve_run_slot.return_value = None
    first_sent = asyncio.Event()
    second_sent = asyncio.Event()
    release_first = asyncio.Event()
    sent = []
    completed = 0

    async def get_state():
        return SimpleNamespace(
            events=[None] * (completed * 2), stats=ConversationStats()
        )

    async def send_message(message, **kwargs):
        sent.append(message.content[0].text)
        (first_sent if len(sent) == 1 else second_sent).set()

    async def wait_for_run_completion(timeout=None):
        nonlocal completed
        if completed == 0:
            await release_first.wait()
        completed += 1
        return ConversationExecutionStatus.FINISHED

    async def get_agent_final_response():
        return "First reply" if completed == 1 else "Second reply"

    event_service.get_state.side_effect = get_state
    event_service.send_message.side_effect = send_message
    event_service.wait_for_run_completion.side_effect = wait_for_run_completion
    event_service.get_agent_final_response.side_effect = get_agent_final_response

    first = asyncio.create_task(
        run_gateway_message(
            SendMessageRequest(content=[TextContent(text="First turn")]),
            event_service,
        )
    )
    await asyncio.wait_for(first_sent.wait(), timeout=1)
    second = asyncio.create_task(
        run_gateway_message(
            SendMessageRequest(content=[TextContent(text="Second turn")]),
            event_service,
        )
    )
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_sent.wait(), timeout=0.05)
    finally:
        release_first.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

    assert sent == ["First turn", "Second turn"]
    replies = [
        result["final_response"] for result in results if isinstance(result, dict)
    ]
    assert replies == [
        "First reply",
        "Second reply",
    ]


@pytest.mark.asyncio
async def test_gateway_lock_wait_expires_before_saving_message(monkeypatch):
    monkeypatch.setattr(event_router, "_GATEWAY_RUN_TIMEOUT_SECONDS", 0.02)
    event_service = _gateway_event_service()

    async with event_service._gateway_run_lock:
        with pytest.raises(HTTPException) as error:
            await run_gateway_message(SendMessageRequest(), event_service)

    assert error.value.status_code == 504
    event_service.send_message.assert_not_awaited()
