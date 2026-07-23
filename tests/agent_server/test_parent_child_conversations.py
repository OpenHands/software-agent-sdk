"""Tests for parent/child conversation relationships."""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openhands.agent_server.conversation_router import conversation_router
from openhands.agent_server.conversation_service import (
    ConversationService,
    InvalidParentConversation,
)
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import LocalWorkspace


def _start_request(
    workspace_dir: Path,
    parent_conversation_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        parent_conversation_id=parent_conversation_id,
        conversation_id=conversation_id,
    )


@pytest.fixture
def workspace_dir(tmp_path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.mark.asyncio
async def test_start_conversation_records_parent(tmp_path, workspace_dir):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_info, is_new = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

        assert is_new
        assert child_info.parent_conversation_id == parent_info.id
        assert child_info.sub_conversation_ids == []


@pytest.mark.asyncio
async def test_parent_lists_children_and_children_report_parent(
    tmp_path, workspace_dir
):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_a, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )
        child_b, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

        parent = await service.get_conversation(parent_info.id)
        assert parent is not None
        assert sorted(parent.sub_conversation_ids, key=str) == sorted(
            [child_a.id, child_b.id], key=str
        )
        assert parent.parent_conversation_id is None

        for child_id in (child_a.id, child_b.id):
            child = await service.get_conversation(child_id)
            assert child is not None
            assert child.parent_conversation_id == parent_info.id


@pytest.mark.asyncio
async def test_relationship_survives_service_reload(tmp_path, workspace_dir):
    conversations_dir = tmp_path / "conversations"
    async with ConversationService(conversations_dir=conversations_dir) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_info, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

    # Fresh service over the same directory exercises _load_catalog_sync.
    async with ConversationService(conversations_dir=conversations_dir) as service:
        parent = await service.get_conversation(parent_info.id)
        child = await service.get_conversation(child_info.id)
        assert parent is not None and child is not None
        assert child.parent_conversation_id == parent_info.id
        assert parent.sub_conversation_ids == [child_info.id]


@pytest.mark.asyncio
async def test_unknown_parent_is_rejected(tmp_path, workspace_dir):
    missing_parent_id = uuid4()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        with pytest.raises(InvalidParentConversation, match=str(missing_parent_id)):
            await service.start_conversation(
                _start_request(workspace_dir, parent_conversation_id=missing_parent_id)
            )


@pytest.mark.asyncio
async def test_self_parent_is_rejected(tmp_path, workspace_dir):
    conversation_id = uuid4()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        with pytest.raises(InvalidParentConversation, match="its own parent"):
            await service.start_conversation(
                _start_request(
                    workspace_dir,
                    parent_conversation_id=conversation_id,
                    conversation_id=conversation_id,
                )
            )


@pytest.mark.asyncio
async def test_cross_workspace_parent_is_rejected(tmp_path):
    workspace_a = tmp_path / "workspace-a"
    workspace_a.mkdir()
    workspace_b = tmp_path / "workspace-b"
    workspace_b.mkdir()
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_a))
        with pytest.raises(InvalidParentConversation, match="different workspace"):
            await service.start_conversation(
                _start_request(workspace_b, parent_conversation_id=parent_info.id)
            )


@pytest.mark.asyncio
async def test_delete_parent_orphans_children(tmp_path, workspace_dir):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_a, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )
        child_b, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

        assert await service.delete_conversation(parent_info.id)

        # Children survive as top-level conversations; the pointer dangles.
        for child_id in (child_a.id, child_b.id):
            child = await service.get_conversation(child_id)
            assert child is not None
            assert child.parent_conversation_id == parent_info.id


@pytest.mark.asyncio
async def test_delete_child_shrinks_parent_children(tmp_path, workspace_dir):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_a, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )
        child_b, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

        assert await service.delete_conversation(child_a.id)

        parent = await service.get_conversation(parent_info.id)
        assert parent is not None
        assert parent.sub_conversation_ids == [child_b.id]


@pytest.mark.asyncio
async def test_search_conversations_include_children(tmp_path, workspace_dir):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        parent_info, _ = await service.start_conversation(_start_request(workspace_dir))
        child_info, _ = await service.start_conversation(
            _start_request(workspace_dir, parent_conversation_id=parent_info.id)
        )

        page = await service.search_conversations()
        by_id = {info.id: info for info in page.items}
        assert by_id[parent_info.id].sub_conversation_ids == [child_info.id]
        assert by_id[child_info.id].parent_conversation_id == parent_info.id


@pytest.mark.asyncio
async def test_top_level_conversation_has_no_relationships(tmp_path, workspace_dir):
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        info, _ = await service.start_conversation(_start_request(workspace_dir))
        assert info.parent_conversation_id is None
        assert info.sub_conversation_ids == []


def test_router_maps_invalid_parent_to_422(tmp_path):
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    service = AsyncMock(spec=ConversationService)
    service.start_conversation.side_effect = InvalidParentConversation(
        f"Parent conversation {uuid4()} not found"
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    client = TestClient(app)

    payload = _start_request(tmp_path, parent_conversation_id=uuid4()).model_dump(
        mode="json", exclude_defaults=False
    )
    response = client.post("/api/conversations", json=payload)

    assert response.status_code == 422
    assert "not found" in response.json()["detail"]
