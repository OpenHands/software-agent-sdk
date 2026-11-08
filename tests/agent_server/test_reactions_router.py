"""Tests for POST /api/conversations/{id}/reactions."""

from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import PersistedSettings, get_settings_store
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.event import MessageEvent, ReactionEvent
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
async def service(tmp_path) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        yield service


def _set_reactions(enabled: bool) -> None:
    def apply(settings: PersistedSettings) -> PersistedSettings:
        settings.experimental_features = {"conversation_reactions": enabled}
        return settings

    get_settings_store().update(apply)


@pytest.fixture(autouse=True)
def reactions_on():
    _set_reactions(True)


@pytest.fixture
async def client(service) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    app.include_router(event_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _conversation_with_answer(service, tmp_path) -> tuple[UUID, MessageEvent]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
        )
    )
    event_service = await service.get_event_service(info.id)
    assert event_service is not None
    answer = MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text="Done.")]),
    )
    conversation = event_service.get_conversation()
    with conversation.state:
        conversation._on_event(answer)
    return info.id, answer


async def _reactions(service, cid: UUID) -> list[ReactionEvent]:
    event_service = await service.get_event_service(cid)
    assert event_service is not None
    state = await event_service.get_state()
    return [e for e in state.events if isinstance(e, ReactionEvent)]


@pytest.mark.asyncio
async def test_person_reacts_and_unreacts(client, service, tmp_path):
    cid, answer = await _conversation_with_answer(service, tmp_path)

    added = await client.post(
        f"/api/conversations/{cid}/reactions", json={"emoji": "+1"}
    )
    removed = await client.post(
        f"/api/conversations/{cid}/reactions",
        json={"emoji": "+1", "target_event_id": answer.id, "removed": True},
    )

    assert added.status_code == 200, added.text
    assert added.json()["kind"] == "ReactionEvent"
    assert added.json()["target_event_id"] == answer.id
    assert added.json()["source"] == "user"
    assert removed.json()["removed"] is True
    saved = await _reactions(service, cid)
    assert [(e.emoji, e.removed) for e in saved] == [("+1", False), ("+1", True)]


@pytest.mark.asyncio
async def test_bad_reactions_are_refused(client, service, tmp_path):
    cid, _ = await _conversation_with_answer(service, tmp_path)

    unknown_emoji = await client.post(
        f"/api/conversations/{cid}/reactions", json={"emoji": "rocket"}
    )
    unknown_message = await client.post(
        f"/api/conversations/{cid}/reactions",
        json={"emoji": "eyes", "target_event_id": "missing"},
    )

    assert unknown_emoji.status_code == 422
    assert unknown_message.status_code == 400
    assert await _reactions(service, cid) == []


@pytest.mark.asyncio
async def test_reactions_are_refused_while_the_feature_is_off(
    client, service, tmp_path
):
    cid, _ = await _conversation_with_answer(service, tmp_path)
    _set_reactions(False)

    response = await client.post(
        f"/api/conversations/{cid}/reactions", json={"emoji": "+1"}
    )

    assert response.status_code == 409
    assert await _reactions(service, cid) == []
