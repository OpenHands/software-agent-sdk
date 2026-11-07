"""Tests for the projects router.

Projects are stored in ``projects.json``; membership is the ``project`` tag on
each conversation. These tests drive the HTTP surface against a real
``ConversationService`` so tag changes go through the same persistence path
as ``PATCH /api/conversations/{id}``.
"""

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.models import (
    StartConversationRequest,
    UpdateConversationRequest,
)
from openhands.agent_server.persistence import reset_stores
from openhands.agent_server.projects_router import projects_router
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture
def persistence_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    path = tmp_path / "persist"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(path))
    reset_stores()
    yield path
    reset_stores()


@pytest.fixture
async def service(tmp_path) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations"
    ) as service:
        yield service


@pytest.fixture
async def client(service, persistence_dir) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.state.config = Config(
        static_files_path=None, session_api_keys=[], secret_key=None
    )
    app.include_router(projects_router, prefix="/api")
    app.dependency_overrides[get_conversation_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _start(
    service: ConversationService,
    tmp_path: Path,
    *,
    title: str | None = None,
    tags: dict[str, str] | None = None,
    parent: UUID | None = None,
) -> UUID:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    info, _ = await service.start_conversation(
        StartConversationRequest(
            agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
            workspace=LocalWorkspace(working_dir=str(workspace)),
            tags=tags or {},
            parent_conversation_id=parent,
        )
    )
    if title is not None:
        await service.update_conversation(
            info.id, UpdateConversationRequest(title=title)
        )
    return info.id


async def _set_status(
    service: ConversationService,
    conversation_id: UUID,
    status: ConversationExecutionStatus,
) -> None:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    state = await event_service.get_state()
    with state:
        state.execution_status = status


async def _tags(service: ConversationService, conversation_id: UUID) -> dict:
    info = await service.get_conversation(conversation_id)
    assert info is not None
    return dict(info.tags)


# Project records


@pytest.mark.asyncio
async def test_list_is_empty_and_creates_no_file(client, persistence_dir):
    response = await client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == {"projects": []}
    assert not (persistence_dir / "projects.json").exists()


@pytest.mark.asyncio
async def test_create_get_list_and_persist(client, persistence_dir):
    created = await client.post(
        "/api/projects", json={"name": "Projects UI", "description": "Board"}
    )

    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Projects UI"
    assert project["description"] == "Board"
    assert project["coordinator_conversation_id"] is None
    assert UUID(project["id"]).hex == project["id"]

    fetched = await client.get(f"/api/projects/{project['id']}")
    assert fetched.json() == project
    listed = await client.get("/api/projects")
    assert listed.json() == {"projects": [project]}

    on_disk = json.loads((persistence_dir / "projects.json").read_text())
    assert on_disk["schema_version"] == 1
    assert on_disk["projects"] == [project]


@pytest.mark.asyncio
async def test_create_rejects_blank_name(client):
    response = await client.post("/api/projects", json={"name": ""})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_project_is_404_everywhere(client, service, tmp_path):
    conversation_id = await _start(service, tmp_path)
    missing = uuid4().hex

    assert (await client.get(f"/api/projects/{missing}")).status_code == 404
    assert (
        await client.patch(f"/api/projects/{missing}", json={"name": "x"})
    ).status_code == 404
    assert (await client.delete(f"/api/projects/{missing}")).status_code == 404
    assert (await client.get(f"/api/projects/{missing}/threads")).status_code == 404
    assert (
        await client.put(f"/api/projects/{missing}/threads/{conversation_id.hex}")
    ).status_code == 404
    assert await _tags(service, conversation_id) == {}


@pytest.mark.asyncio
async def test_patch_renames_and_bumps_updated_at(client):
    project = (await client.post("/api/projects", json={"name": "Old"})).json()

    response = await client.patch(
        f"/api/projects/{project['id']}", json={"name": "New"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "New"
    assert body["description"] == ""
    assert body["created_at"] == project["created_at"]
    assert body["updated_at"] > project["updated_at"]


@pytest.mark.asyncio
async def test_delete_removes_project_and_keeps_conversations(
    client, service, tmp_path
):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    conversation_id = await _start(service, tmp_path)
    await client.put(f"/api/projects/{project['id']}/threads/{conversation_id.hex}")

    response = await client.delete(f"/api/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert (await client.get("/api/projects")).json() == {"projects": []}
    assert await service.get_conversation(conversation_id) is not None
    assert await _tags(service, conversation_id) == {"project": project["id"]}


@pytest.mark.asyncio
async def test_corrupted_file_is_never_overwritten(client, persistence_dir):
    persistence_dir.mkdir(parents=True, exist_ok=True)
    path = persistence_dir / "projects.json"
    path.write_text("{not json")

    assert (await client.get("/api/projects")).status_code == 409
    assert (await client.post("/api/projects", json={"name": "P"})).status_code == 409
    assert path.read_text() == "{not json"


@pytest.mark.asyncio
async def test_newer_schema_is_refused(client, persistence_dir):
    persistence_dir.mkdir(parents=True, exist_ok=True)
    path = persistence_dir / "projects.json"
    path.write_text(json.dumps({"schema_version": 99, "projects": []}))

    assert (await client.get("/api/projects")).status_code == 409
    assert (await client.post("/api/projects", json={"name": "P"})).status_code == 409


# Coordinator


@pytest.mark.asyncio
async def test_create_with_coordinator_tags_it_and_keeps_other_tags(
    client, service, tmp_path
):
    cat = await _start(service, tmp_path, tags={"insidercat": "self"})

    response = await client.post(
        "/api/projects",
        json={"name": "P", "coordinator_conversation_id": str(cat)},
    )

    assert response.status_code == 201
    project = response.json()
    assert project["coordinator_conversation_id"] == cat.hex
    assert await _tags(service, cat) == {"insidercat": "self", "project": project["id"]}

    threads = (await client.get(f"/api/projects/{project['id']}/threads")).json()
    assert threads["coordinator"]["conversation_id"] == cat.hex
    assert threads["threads"] == []


@pytest.mark.asyncio
async def test_create_with_missing_coordinator_writes_nothing(client, persistence_dir):
    response = await client.post(
        "/api/projects",
        json={"name": "P", "coordinator_conversation_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert not (persistence_dir / "projects.json").exists()


@pytest.mark.asyncio
async def test_patch_sets_and_clears_coordinator(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cat = await _start(service, tmp_path)

    set_response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"coordinator_conversation_id": cat.hex},
    )
    assert set_response.json()["coordinator_conversation_id"] == cat.hex
    assert (await _tags(service, cat))["project"] == project["id"]

    # Omitting the field leaves the coordinator alone.
    renamed = await client.patch(f"/api/projects/{project['id']}", json={"name": "Q"})
    assert renamed.json()["coordinator_conversation_id"] == cat.hex

    cleared = await client.patch(
        f"/api/projects/{project['id']}",
        json={"coordinator_conversation_id": None},
    )
    assert cleared.json()["coordinator_conversation_id"] is None
    # The former coordinator stays a member, now listed as a thread.
    threads = (await client.get(f"/api/projects/{project['id']}/threads")).json()
    assert threads["coordinator"] is None
    assert [t["conversation_id"] for t in threads["threads"]] == [cat.hex]


@pytest.mark.asyncio
async def test_a_coordinator_leads_one_project(client, service, tmp_path):
    cat = await _start(service, tmp_path)
    first = (
        await client.post(
            "/api/projects", json={"name": "A", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    second = (await client.post("/api/projects", json={"name": "B"})).json()

    as_coordinator = await client.post(
        "/api/projects", json={"name": "C", "coordinator_conversation_id": cat.hex}
    )
    as_thread = await client.put(f"/api/projects/{second['id']}/threads/{cat.hex}")

    assert as_coordinator.status_code == 409
    assert as_thread.status_code == 409
    assert (await _tags(service, cat))["project"] == first["id"]


# Threads


@pytest.mark.asyncio
async def test_threads_are_grouped_by_attention(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    statuses = {
        "idle": ConversationExecutionStatus.IDLE,
        "done": ConversationExecutionStatus.FINISHED,
        "asks": ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
        "busy": ConversationExecutionStatus.RUNNING,
        "broken": ConversationExecutionStatus.ERROR,
    }
    ids: dict[str, UUID] = {}
    for title, status in statuses.items():
        ids[title] = await _start(service, tmp_path, title=title)
        await _set_status(service, ids[title], status)
        added = await client.put(
            f"/api/projects/{project['id']}/threads/{ids[title].hex}"
        )
        assert added.status_code == 204
    outsider = await _start(service, tmp_path, title="outsider")

    response = await client.get(f"/api/projects/{project['id']}/threads")

    assert response.status_code == 200
    threads = response.json()["threads"]
    # Within a group, newest-updated first: "broken" was added after "asks".
    assert [(t["title"], t["attention"]) for t in threads] == [
        ("broken", "needs_you"),
        ("asks", "needs_you"),
        ("busy", "working"),
        ("done", "finished"),
        ("idle", "idle"),
    ]
    assert outsider.hex not in {t["conversation_id"] for t in threads}
    assert threads[0]["execution_status"] == "error"


@pytest.mark.asyncio
async def test_tagged_children_are_threads_with_their_parent(client, service, tmp_path):
    cat = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    # A child copies its parent's tags, as ``start_child_conversation`` does.
    child = await _start(
        service, tmp_path, tags=await _tags(service, cat), parent=cat, title="child"
    )

    threads = (await client.get(f"/api/projects/{project['id']}/threads")).json()

    assert [
        (t["conversation_id"], t["parent_conversation_id"]) for t in threads["threads"]
    ] == [(child.hex, cat.hex)]


@pytest.mark.asyncio
async def test_add_thread_is_idempotent_and_moves_between_projects(
    client, service, tmp_path
):
    first = (await client.post("/api/projects", json={"name": "A"})).json()
    second = (await client.post("/api/projects", json={"name": "B"})).json()
    worker = await _start(service, tmp_path, tags={"insiderrole": "worker"})

    for _ in range(2):
        response = await client.put(f"/api/projects/{first['id']}/threads/{worker.hex}")
        assert response.status_code == 204
    moved = await client.put(f"/api/projects/{second['id']}/threads/{worker.hex}")

    assert moved.status_code == 204
    assert await _tags(service, worker) == {
        "insiderrole": "worker",
        "project": second["id"],
    }
    first_threads = await client.get(f"/api/projects/{first['id']}/threads")
    assert first_threads.json()["threads"] == []


@pytest.mark.asyncio
async def test_add_missing_conversation_is_404(client):
    project = (await client.post("/api/projects", json={"name": "P"})).json()

    response = await client.put(f"/api/projects/{project['id']}/threads/{uuid4().hex}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


@pytest.mark.asyncio
async def test_remove_thread_clears_only_the_project_tag(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    worker = await _start(service, tmp_path, tags={"insiderrole": "worker"})
    await client.put(f"/api/projects/{project['id']}/threads/{worker.hex}")

    removed = await client.delete(f"/api/projects/{project['id']}/threads/{worker.hex}")
    again = await client.delete(f"/api/projects/{project['id']}/threads/{worker.hex}")

    assert removed.status_code == 200
    assert again.status_code == 404
    assert await _tags(service, worker) == {"insiderrole": "worker"}


@pytest.mark.asyncio
async def test_remove_leaves_a_member_of_another_project_alone(
    client, service, tmp_path
):
    first = (await client.post("/api/projects", json={"name": "A"})).json()
    second = (await client.post("/api/projects", json={"name": "B"})).json()
    worker = await _start(service, tmp_path)
    await client.put(f"/api/projects/{second['id']}/threads/{worker.hex}")

    response = await client.delete(f"/api/projects/{first['id']}/threads/{worker.hex}")

    assert response.status_code == 404
    assert (await _tags(service, worker))["project"] == second["id"]


@pytest.mark.asyncio
async def test_coordinator_cannot_be_removed_as_thread(client, service, tmp_path):
    cat = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()

    response = await client.delete(f"/api/projects/{project['id']}/threads/{cat.hex}")

    assert response.status_code == 409
    assert (await _tags(service, cat))["project"] == project["id"]


@pytest.mark.asyncio
async def test_membership_survives_service_restart(persistence_dir, tmp_path):
    conversations_dir = tmp_path / "conversations"
    async with ConversationService(conversations_dir=conversations_dir) as service:
        worker = await _start(service, tmp_path)
        await service.set_conversation_tag(worker, "project", "abc")

    async with ConversationService(conversations_dir=conversations_dir) as service:
        members = await service.list_tagged_conversations("project", "abc")

    assert [stored.id for stored, _ in members] == [worker]
