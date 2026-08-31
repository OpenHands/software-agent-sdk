"""Integration tests for reversible conversation archiving."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent, Message
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


@pytest_asyncio.fixture
async def conversation_service(tmp_path: Path) -> AsyncIterator[ConversationService]:
    service = ConversationService(conversations_dir=tmp_path / "conversations")
    async with service:
        yield service


@pytest_asyncio.fixture
async def client(
    conversation_service: ConversationService,
) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    app.include_router(conversation_router, prefix="/api")
    app.include_router(event_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: conversation_service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _create_conversation(
    service: ConversationService, workspace: Path, *, title: str
):
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="test-model", usage_id="test"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            confirmation_policy=NeverConfirm(),
            autotitle=False,
            title=title,
            tags={"owner": "agent-a", "purpose": "archive-test"},
        )
    )
    event_service = await service.get_event_service(info.id)
    assert event_service is not None
    await event_service.send_message(Message(role="user", content="keep this history"))
    return info


@pytest.mark.asyncio
async def test_archive_unarchive_is_reversible_filtered_and_idempotent(
    client: httpx.AsyncClient,
    conversation_service: ConversationService,
    tmp_path: Path,
):
    archived = await _create_conversation(
        conversation_service, tmp_path / "archived-workspace", title="Archive me"
    )
    active = await _create_conversation(
        conversation_service, tmp_path / "active-workspace", title="Keep active"
    )
    updated = await client.patch(
        f"/api/conversations/{archived.id}", json={"title": "Archive me"}
    )
    assert updated.status_code == 200

    events_before = await client.get(f"/api/conversations/{archived.id}/events/search")
    assert events_before.status_code == 200
    history_before = events_before.json()["items"]
    assert history_before

    missing_confirmation = await client.post(
        f"/api/conversations/{archived.id}/archive", json={}
    )
    assert missing_confirmation.status_code == 422

    response = await client.post(
        f"/api/conversations/{archived.id}/archive", json={"confirmed": True}
    )
    assert response.status_code == 200
    first_archived_at = (await client.get(f"/api/conversations/{archived.id}")).json()[
        "archived_at"
    ]
    response = await client.post(
        f"/api/conversations/{archived.id}/archive", json={"confirmed": True}
    )
    assert response.status_code == 200

    direct = await client.get(f"/api/conversations/{archived.id}")
    assert direct.json()["archived_at"] == first_archived_at
    assert direct.status_code == 200
    assert direct.json()["archived_at"] is not None
    assert direct.json()["title"] == "Archive me"
    assert direct.json()["tags"] == {
        "owner": "agent-a",
        "purpose": "archive-test",
    }
    meta_file = conversation_service.conversations_dir / archived.id.hex / "meta.json"
    persisted = json.loads(meta_file.read_text())
    assert persisted["archived_at"] == first_archived_at
    assert persisted["tags"] == direct.json()["tags"]

    default_page = await client.get("/api/conversations/search")
    assert [item["id"] for item in default_page.json()["items"]] == [str(active.id)]

    archived_page = await client.get(
        "/api/conversations/search", params={"archive_filter": "ARCHIVED"}
    )
    assert [item["id"] for item in archived_page.json()["items"]] == [str(archived.id)]

    all_page = await client.get(
        "/api/conversations/search", params={"archive_filter": "ALL"}
    )
    assert {item["id"] for item in all_page.json()["items"]} == {
        str(active.id),
        str(archived.id),
    }

    events_after = await client.get(f"/api/conversations/{archived.id}/events/search")
    assert events_after.status_code == 200
    assert events_after.json()["items"] == history_before

    for _ in range(2):
        response = await client.post(f"/api/conversations/{archived.id}/unarchive")
        assert response.status_code == 200

    restored = await client.get(f"/api/conversations/{archived.id}")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["tags"] == {
        "owner": "agent-a",
        "purpose": "archive-test",
    }
    default_page = await client.get("/api/conversations/search")
    assert {item["id"] for item in default_page.json()["items"]} == {
        str(active.id),
        str(archived.id),
    }


@pytest.mark.asyncio
async def test_delete_remains_permanent_and_separate_from_archive(
    client: httpx.AsyncClient,
    conversation_service: ConversationService,
    tmp_path: Path,
):
    conversation = await _create_conversation(
        conversation_service, tmp_path / "delete-workspace", title="Delete me"
    )
    conversation_dir = conversation_service.conversations_dir / conversation.id.hex

    archived = await client.post(
        f"/api/conversations/{conversation.id}/archive", json={"confirmed": True}
    )
    assert archived.status_code == 200
    assert conversation_dir.is_dir()

    deleted = await client.delete(f"/api/conversations/{conversation.id}")
    assert deleted.status_code == 200
    assert not conversation_dir.exists()
    assert (
        await client.get(f"/api/conversations/{conversation.id}")
    ).status_code == 404
    archived_page = await client.get(
        "/api/conversations/search", params={"archive_filter": "ARCHIVED"}
    )
    assert archived_page.json()["items"] == []
    assert (
        await client.post(f"/api/conversations/{conversation.id}/unarchive")
    ).status_code == 404
