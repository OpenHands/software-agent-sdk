"""Voice admission against a real conversation waiting for confirmation.

Running a conversation that waits for confirmation approves its pending
actions. Voice must never do that: a spoken request is admitted only while the
conversation is idle, checked atomically with the append and the run start.
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from openhands.agent_server.api import create_app
from openhands.agent_server.codex_voice import CodexRelay
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.sdk import Message, TextContent, Tool
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.security.confirmation_policy import AlwaysConfirm
from openhands.sdk.testing import TestLLM
from openhands.tools.terminal import TerminalTool
from tests.agent_server.stress.scripts import (
    start_conversation_with_test_llm,
    text_message,
)
from tests.agent_server.test_codex_voice import (  # noqa: F401 - fixture
    _handoff,
    _speeches,
    _status,
    protocol,
)


VOICE_TEXT = "Voice: summarize the plan. Do not approve anything."


def _user(text: str) -> Message:
    return Message(role="user", content=[TextContent(text=text)])


def _tool_call(marker: Path) -> Message:
    return Message(
        role="assistant",
        content=[TextContent(text="Writing the marker.")],
        tool_calls=[
            MessageToolCall(
                id="call-marker",
                name="terminal",
                arguments=json.dumps({"command": f"echo approved > {marker}"}),
                origin="completion",
            )
        ],
    )


@pytest_asyncio.fixture
async def conversation_service(tmp_path):
    persist = tmp_path / "persist"
    persist.mkdir()
    service = ConversationService(conversations_dir=persist)
    async with service:
        yield service


async def _service(
    conversation_service: ConversationService, tmp_path: Path, marker: Path
) -> EventService:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    llm = TestLLM.from_messages(
        [_tool_call(marker), text_message("Done."), text_message("Done.")]
    )
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=llm,
        workspace_dir=str(workspace),
        usage_id="voice-admission",
        tools=[Tool(name=TerminalTool.name)],
        initial_text=None,
    )
    service = await conversation_service.get_event_service(info.id)
    assert service is not None
    service.get_conversation().set_confirmation_policy(AlwaysConfirm())
    return service


async def _type_until_confirmation(service: EventService) -> None:
    """The person types a request whose tool call waits for confirmation."""
    await service.send_message(_user("Typed: write the marker."), run=True)
    async with asyncio.timeout(20):
        while True:
            try:
                status = await service.wait_for_run_completion(timeout=0)
            except TimeoutError:
                status = None
            if status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION:
                return
            await asyncio.sleep(0.02)


def _voice_saved(state: ConversationState) -> bool:
    with state:
        return any(
            isinstance(event, MessageEvent)
            and event.llm_message.role == "user"
            and VOICE_TEXT in str(event.llm_message.content)
            for event in state.events
        )


@pytest.mark.timeout(60)
async def test_idle_admission_refuses_pending_confirmation(
    conversation_service, tmp_path
):
    marker = tmp_path / "marker.txt"
    service = await _service(conversation_service, tmp_path, marker)
    await _type_until_confirmation(service)

    with pytest.raises(ValueError, match="waiting_for_confirmation"):
        await service.send_message_if_idle(_user(VOICE_TEXT))

    await asyncio.sleep(0.5)
    state = await service.get_state()
    assert not marker.exists()
    assert not _voice_saved(state)
    assert (
        await service.wait_for_run_completion(timeout=0)
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )


@pytest.mark.timeout(60)
async def test_idle_admission_refuses_paused_pending_actions(
    conversation_service, tmp_path
):
    marker = tmp_path / "marker.txt"
    service = await _service(conversation_service, tmp_path, marker)
    await _type_until_confirmation(service)
    await service.pause()

    with pytest.raises(ValueError, match="waiting_for_confirmation"):
        await service.send_message_if_idle(_user(VOICE_TEXT))

    await asyncio.sleep(0.5)
    assert not marker.exists()
    assert not _voice_saved(await service.get_state())


@pytest.mark.timeout(60)
async def test_codex_voice_cannot_approve_after_typed_input_races_preflight(
    protocol,  # noqa: F811 - fixture
    conversation_service,
    tmp_path,
):
    """Typed input reaches a confirmation after Voice's idle preflight passed."""
    marker = tmp_path / "marker.txt"
    service = await _service(conversation_service, tmp_path, marker)
    real_get_state = service.get_state
    raced = False

    async def get_state_after_typed_race():
        nonlocal raced
        if not raced:
            raced = True
            await _type_until_confirmation(service)
        return await real_get_state()

    service.get_state = get_state_after_typed_race  # type: ignore[method-assign]
    relay = CodexRelay(service.stored.id, service, tmp_path / "home")
    try:
        await relay.start("v=0", "")
        await relay.rpc("fixture/notify", _handoff(input_transcript=VOICE_TEXT))
        speech = (await _speeches(relay, 1))[0]
        await _status(relay, "error")
        assert "not sent" in speech
        assert relay.status.error_code == "request_not_sent"
    finally:
        await relay.close()

    await asyncio.sleep(0.5)
    assert raced
    assert not marker.exists()
    assert not _voice_saved(await real_get_state())
    assert (
        await service.wait_for_run_completion(timeout=0)
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )


@pytest.mark.timeout(60)
async def test_idle_admission_saves_and_runs_an_idle_conversation(
    conversation_service, tmp_path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    info = await start_conversation_with_test_llm(
        conversation_service,
        parent_llm=TestLLM.from_messages([text_message("Heard you.")]),
        workspace_dir=str(workspace),
        usage_id="voice-admission-idle",
        initial_text=None,
    )
    service = await conversation_service.get_event_service(info.id)
    assert service is not None

    await service.send_message_if_idle(_user(VOICE_TEXT))
    assert (
        await service.wait_for_run_completion(timeout=20)
        == ConversationExecutionStatus.FINISHED
    )
    assert _voice_saved(await service.get_state())


@pytest.mark.timeout(60)
async def test_browser_voice_message_cannot_approve_pending_confirmation(
    conversation_service, tmp_path, monkeypatch
):
    """The OpenAI transport delegates through POST .../voice/messages."""
    marker = tmp_path / "marker.txt"
    service = await _service(conversation_service, tmp_path, marker)
    await _type_until_confirmation(service)

    async def insider(conversation_id, _service):
        return None

    monkeypatch.setattr("openhands.agent_server.voice_router._insider", insider)
    app = create_app(Config(session_api_keys=[]))
    app.dependency_overrides[get_conversation_service] = lambda: conversation_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(
            f"/api/conversations/{service.stored.id}/voice/messages",
            json={"text": VOICE_TEXT},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "waiting_for_confirmation"

    await asyncio.sleep(0.5)
    assert not marker.exists()
    assert not _voice_saved(await service.get_state())
    assert (
        await service.wait_for_run_completion(timeout=0)
        == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
