"""Tests for parent/child conversation relationships."""

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch
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
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    ConversationInfo,
    StartChildConversationRequest,
    StartChildConversationResponse,
    StartConversationRequest,
)
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.git.utils import run_git_command
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


# ---------------------------------------------------------------------------
# Server-side child launches (start_child_conversation)
# ---------------------------------------------------------------------------


def _init_git_repo(repo_dir: Path) -> None:
    repo_dir.mkdir()
    (repo_dir / "README.md").write_text("# test repo\n")
    run_git_command(["git", "init", "-b", "main"], repo_dir)
    run_git_command(["git", "add", "README.md"], repo_dir)
    run_git_command(
        [
            "git",
            "-c",
            "user.name=OpenHands Test",
            "-c",
            "user.email=openhands@example.com",
            "commit",
            "-m",
            "init",
        ],
        repo_dir,
    )


def _event_service_factory(services: dict[UUID, Any]):
    """Stand in for EventService so no agent loop (and no LLM call) runs."""

    def factory(**kwargs):
        stored = kwargs["stored"]
        agent = cast(AgentBase, kwargs.get("agent"))
        event_service = AsyncMock(spec=EventService)
        event_service.stored = stored
        event_service.get_conversation.return_value = SimpleNamespace(agent=agent)
        event_service.get_state.return_value = ConversationState(
            id=stored.id,
            agent=agent,
            workspace=stored.workspace,
            execution_status=ConversationExecutionStatus.IDLE,
            confirmation_policy=stored.confirmation_policy,
        )
        services[stored.id] = event_service
        return event_service

    return factory


@dataclass
class _ChildLaunch:
    parent_id: UUID
    result: StartChildConversationResponse | None
    parent: ConversationInfo | None
    child: ConversationInfo | None
    services: dict[UUID, Any]
    worktree_root: Path


async def _launch_child(
    tmp_path: Path, workspace_dir: Path, request: StartChildConversationRequest
) -> _ChildLaunch:
    """Start a parent in *workspace_dir*, then launch a child from it."""
    services: dict[UUID, Any] = {}
    worktree_root = tmp_path / "conversation-worktrees"
    parent_request = _start_request(workspace_dir).model_copy(
        update={"tags": {"source": "test"}}
    )
    with patch(
        "openhands.agent_server.conversation_service.EventService",
        side_effect=_event_service_factory(services),
    ):
        async with ConversationService(
            conversations_dir=tmp_path / "conversations",
            conversation_worktree_root=worktree_root,
        ) as service:
            parent_info, _ = await service.start_conversation(parent_request)
            result = await service.start_child_conversation(parent_info.id, request)
            parent = await service.get_conversation(parent_info.id)
            child = (
                await service.get_conversation(result.conversation_id)
                if result
                else None
            )
    return _ChildLaunch(
        parent_id=parent_info.id,
        result=result,
        parent=parent,
        child=child,
        services=services,
        worktree_root=worktree_root,
    )


@pytest.mark.asyncio
async def test_start_child_conversation_links_child_to_parent(tmp_path, workspace_dir):
    request = StartChildConversationRequest(task="Write the docs")

    launch = await _launch_child(tmp_path, workspace_dir, request)

    assert launch.result is not None
    assert launch.result.parent_conversation_id == launch.parent_id
    assert launch.parent is not None
    assert launch.parent.sub_conversation_ids == [launch.result.conversation_id]
    assert launch.child is not None
    assert launch.child.parent_conversation_id == launch.parent_id


@pytest.mark.asyncio
async def test_start_child_conversation_delivers_task_as_first_message(
    tmp_path, workspace_dir
):
    request = StartChildConversationRequest(task="Write the docs")

    launch = await _launch_child(tmp_path, workspace_dir, request)

    assert launch.result is not None
    child_service = launch.services[launch.result.conversation_id]
    child_service.send_message.assert_awaited_once_with(
        Message(role="user", content=[TextContent(text="Write the docs")]), True
    )


@pytest.mark.asyncio
async def test_start_child_conversation_inherits_parent_configuration(
    tmp_path, workspace_dir
):
    request = StartChildConversationRequest(task="Write the docs", title="Docs")

    launch = await _launch_child(tmp_path, workspace_dir, request)

    assert launch.result is not None
    child_stored = launch.services[launch.result.conversation_id].stored
    assert child_stored.tags == {"source": "test"}
    assert child_stored.title == "Docs"
    assert launch.result.title == "Docs"


@pytest.mark.asyncio
async def test_start_child_conversation_shares_workspace_outside_git_repo(
    tmp_path, workspace_dir
):
    request = StartChildConversationRequest(task="Write the docs", isolation="worktree")

    launch = await _launch_child(tmp_path, workspace_dir, request)

    assert launch.result is not None
    assert launch.result.isolation == "shared"
    assert launch.result.workspace == str(workspace_dir)


@pytest.mark.asyncio
async def test_start_child_conversation_uses_worktree_in_git_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    request = StartChildConversationRequest(task="Fix the bug", isolation="worktree")

    launch = await _launch_child(tmp_path, repo_dir, request)

    assert launch.result is not None
    child_id = launch.result.conversation_id
    expected_worktree = launch.worktree_root / str(child_id) / repo_dir.name
    assert launch.result.isolation == "worktree"
    assert launch.result.workspace == str(expected_worktree)
    assert (expected_worktree / ".git").exists()
    assert (
        run_git_command(
            ["git", "--no-pager", "branch", "--show-current"], expected_worktree
        )
        == f"openhands/{child_id}"
    )


@pytest.mark.asyncio
async def test_start_child_conversation_shared_isolation_keeps_parent_directory(
    tmp_path,
):
    repo_dir = tmp_path / "repo"
    _init_git_repo(repo_dir)
    request = StartChildConversationRequest(task="Fix the bug", isolation="shared")

    launch = await _launch_child(tmp_path, repo_dir, request)

    assert launch.result is not None
    assert launch.result.isolation == "shared"
    assert launch.result.workspace == str(repo_dir)
    assert not (launch.worktree_root / str(launch.result.conversation_id)).exists()


@pytest.mark.asyncio
async def test_start_child_conversation_unknown_parent_returns_none(tmp_path):
    request = StartChildConversationRequest(task="anything")

    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        result = await service.start_child_conversation(uuid4(), request)

    assert result is None


def _child_client(service: ConversationService) -> TestClient:
    app = FastAPI()
    app.include_router(conversation_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    return TestClient(app)


def test_router_creates_child_conversation():
    parent_id, child_id = uuid4(), uuid4()
    service = AsyncMock(spec=ConversationService)
    service.start_child_conversation.return_value = StartChildConversationResponse(
        conversation_id=child_id,
        parent_conversation_id=parent_id,
        status="idle",
        title="Docs",
        workspace="/workspace",
        isolation="shared",
    )

    response = _child_client(service).post(
        f"/api/conversations/{parent_id}/children",
        json={"task": "Write the docs", "title": "Docs", "isolation": "shared"},
    )

    assert response.status_code == 201
    assert response.json()["conversation_id"] == str(child_id)
    assert response.json()["parent_conversation_id"] == str(parent_id)
    service.start_child_conversation.assert_awaited_once_with(
        parent_id,
        StartChildConversationRequest(
            task="Write the docs", title="Docs", isolation="shared"
        ),
    )


def test_router_maps_missing_child_parent_to_404():
    service = AsyncMock(spec=ConversationService)
    service.start_child_conversation.return_value = None

    response = _child_client(service).post(
        f"/api/conversations/{uuid4()}/children", json={"task": "anything"}
    )

    assert response.status_code == 404
    assert "Parent conversation not found" in response.json()["detail"]


def test_router_rejects_child_with_empty_task():
    service = AsyncMock(spec=ConversationService)

    response = _child_client(service).post(
        f"/api/conversations/{uuid4()}/children", json={"task": ""}
    )

    assert response.status_code == 422
    service.start_child_conversation.assert_not_awaited()
