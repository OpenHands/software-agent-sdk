"""Tests for POST /api/conversations/{id}/ask_user/answer."""

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import patch
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from litellm.types.utils import (
    Choices,
    Message as LiteLLMMessage,
    ModelResponse,
)

from openhands.agent_server.api import add_domain_error_handler
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    get_conversation_service,
    get_settings_store,
)
from openhands.agent_server.event_router import event_router
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import PersistedSettings, ServerStores
from openhands.sdk import LLM, Agent
from openhands.sdk.event import ActionEvent, MessageEvent, ObservationEvent
from openhands.sdk.event.ask_user import AskUserAnswerEvent, find_pending_ask
from openhands.sdk.llm import MessageToolCall, TextContent
from openhands.sdk.tool import Tool
from openhands.sdk.workspace import LocalWorkspace
from openhands.tools.ask_user import (
    AskUserAction,
    AskUserObservation,
    AskUserOption,
)
from tests.agent_server.conftest import install_test_context


@pytest.fixture
async def service(tmp_path) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        yield service


def _set_feature(stores: ServerStores, enabled: bool) -> None:
    def apply(settings: PersistedSettings) -> PersistedSettings:
        settings.experimental_features = {"ask_user": enabled}
        return settings

    stores.settings.update(apply)


@pytest.fixture(autouse=True)
def feature_on(server_stores: ServerStores):
    _set_feature(server_stores, True)


@pytest.fixture(autouse=True)
def model():
    """The run an answer starts gets a short reply instead of a real model."""
    reply = ModelResponse(
        id="resp-answer",
        choices=[
            Choices(
                index=0,
                message=LiteLLMMessage(role="assistant", content="On it."),
                finish_reason="stop",
            )
        ],
        created=0,
        model="gpt-4o",
        object="chat.completion",
    )

    async def complete(*args, **kwargs):
        return reply

    with patch("openhands.sdk.llm.llm.litellm_acompletion", new=complete):
        yield


@pytest.fixture
async def client(
    service, server_stores: ServerStores
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    add_domain_error_handler(app)
    install_test_context(app)
    app.dependency_overrides[get_settings_store] = lambda: server_stores.settings
    app.include_router(event_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _conversation_with_question(service, tmp_path) -> tuple[UUID, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(
                llm=LLM(model="gpt-4o", usage_id="test-llm", api_key="k"),
                tools=[Tool(name="ask_user")],
            ),
            workspace=LocalWorkspace(working_dir=str(workspace)),
        )
    )
    event_service = await service.get_event_service(info.id)
    assert event_service is not None
    action = AskUserAction(
        question="Ship behind a flag?",
        options=[AskUserOption(label="Flag"), AskUserOption(label="Everyone")],
        recommended=0,
    )
    ask = ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=action,
        tool_name="ask_user",
        tool_call_id="ask-1",
        tool_call=MessageToolCall(
            id="ask-1",
            name="ask_user",
            arguments=action.model_dump_json(exclude={"kind"}),
            origin="completion",
        ),
        llm_response_id="resp-1",
    )
    shown = ObservationEvent(
        source="environment",
        observation=AskUserObservation.from_text("shown"),
        action_id=ask.id,
        tool_name="ask_user",
        tool_call_id="ask-1",
    )
    conversation = event_service.get_conversation()
    with conversation.state:
        conversation._on_event(ask)
        conversation._on_event(shown)
    return info.id, ask.id


async def _events(service, cid: UUID) -> list:
    event_service = await service.get_event_service(cid)
    assert event_service is not None
    return list((await event_service.get_state()).events)


@pytest.mark.asyncio
async def test_a_tap_answers_and_runs_the_agent(client, service, tmp_path):
    cid, ask_id = await _conversation_with_question(service, tmp_path)

    response = await client.post(
        f"/api/conversations/{cid}/ask_user/answer",
        json={"ask_id": ask_id, "option_index": 1},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "AskUserAnswerEvent"
    assert (body["option_index"], body["option_label"]) == (1, "Everyone")
    events: list = []
    for _ in range(100):
        events = await _events(service, cid)
        if any(isinstance(e, MessageEvent) and e.source == "agent" for e in events):
            break
        await asyncio.sleep(0.05)
    answers = [e for e in events if isinstance(e, AskUserAnswerEvent)]
    messages = [e for e in events if isinstance(e, MessageEvent)]
    assert [a.id for a in answers] == [body["id"]]
    # The answer the agent reads, then the agent's reply in the run it started.
    assert [(m.source, m.sender) for m in messages] == [
        ("user", None),
        ("agent", None),
    ]
    assert "Everyone" in messages[0].llm_message.content[0].text  # type: ignore[union-attr]
    assert find_pending_ask(events) is None


@pytest.mark.asyncio
async def test_answers_to_no_pending_question_are_refused(client, service, tmp_path):
    cid, ask_id = await _conversation_with_question(service, tmp_path)

    wrong_question = await client.post(
        f"/api/conversations/{cid}/ask_user/answer",
        json={"ask_id": "missing", "option_index": 0},
    )
    wrong_option = await client.post(
        f"/api/conversations/{cid}/ask_user/answer",
        json={"ask_id": ask_id, "option_index": 7},
    )
    nothing = await client.post(
        f"/api/conversations/{cid}/ask_user/answer", json={"ask_id": ask_id}
    )

    assert [wrong_question.status_code, wrong_option.status_code] == [400, 400]
    assert nothing.status_code == 400
    assert not [
        e for e in await _events(service, cid) if isinstance(e, AskUserAnswerEvent)
    ]


@pytest.mark.asyncio
async def test_answers_are_refused_while_the_feature_is_off(
    client, service, tmp_path, server_stores
):
    cid, ask_id = await _conversation_with_question(service, tmp_path)
    _set_feature(server_stores, False)

    response = await client.post(
        f"/api/conversations/{cid}/ask_user/answer",
        json={"ask_id": ask_id, "option_index": 0},
    )

    assert response.status_code == 409
    assert find_pending_ask(await _events(service, cid)) is not None
