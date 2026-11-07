"""Tests for the projects router.

Projects are stored in ``projects.json``; membership is the ``project`` tag on
each conversation. These tests drive the HTTP surface against a real
``ConversationService`` so tag changes go through the same persistence path
as ``PATCH /api/conversations/{id}``.
"""

import asyncio
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
from openhands.agent_server.event_router import event_router
from openhands.agent_server.event_service import EventService
from openhands.agent_server.models import (
    StartConversationRequest,
    UpdateConversationRequest,
)
from openhands.agent_server.persistence import reset_stores
from openhands.agent_server.project_wake import (
    notify_coordinator,
    relay_person_message,
)
from openhands.agent_server.projects_router import projects_router
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
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
    app.state.conversation_service = service
    app.include_router(projects_router, prefix="/api")
    app.include_router(event_router, prefix="/api")
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
    assert (
        await client.get(f"/api/projects/{missing}/conversations")
    ).status_code == 404
    assert (
        await client.put(f"/api/projects/{missing}/conversations/{conversation_id.hex}")
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
    await client.put(
        f"/api/projects/{project['id']}/conversations/{conversation_id.hex}"
    )

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

    conversations = (
        await client.get(f"/api/projects/{project['id']}/conversations")
    ).json()
    assert conversations["coordinator"]["conversation_id"] == cat.hex
    assert conversations["conversations"] == []


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
    # The former coordinator stays a member, now listed with the others.
    conversations = (
        await client.get(f"/api/projects/{project['id']}/conversations")
    ).json()
    assert conversations["coordinator"] is None
    assert [t["conversation_id"] for t in conversations["conversations"]] == [cat.hex]


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
    as_conversation = await client.put(
        f"/api/projects/{second['id']}/conversations/{cat.hex}"
    )

    assert as_coordinator.status_code == 409
    assert as_conversation.status_code == 409
    assert (await _tags(service, cat))["project"] == first["id"]


# Conversations


@pytest.mark.asyncio
async def test_conversations_are_grouped_by_attention(client, service, tmp_path):
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
            f"/api/projects/{project['id']}/conversations/{ids[title].hex}"
        )
        assert added.status_code == 204
    outsider = await _start(service, tmp_path, title="outsider")

    response = await client.get(f"/api/projects/{project['id']}/conversations")

    assert response.status_code == 200
    conversations = response.json()["conversations"]
    # Within a group, newest-updated first: "broken" was added after "asks".
    assert [(t["title"], t["attention"]) for t in conversations] == [
        ("broken", "needs_you"),
        ("asks", "needs_you"),
        ("busy", "working"),
        ("done", "finished"),
        ("idle", "idle"),
    ]
    assert outsider.hex not in {t["conversation_id"] for t in conversations}
    assert conversations[0]["execution_status"] == "error"


@pytest.mark.asyncio
async def test_tagged_children_are_conversations_with_their_parent(
    client, service, tmp_path
):
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

    conversations = (
        await client.get(f"/api/projects/{project['id']}/conversations")
    ).json()

    assert [
        (t["conversation_id"], t["parent_conversation_id"])
        for t in conversations["conversations"]
    ] == [(child.hex, cat.hex)]


@pytest.mark.asyncio
async def test_add_conversation_is_idempotent_and_moves_between_projects(
    client, service, tmp_path
):
    first = (await client.post("/api/projects", json={"name": "A"})).json()
    second = (await client.post("/api/projects", json={"name": "B"})).json()
    worker = await _start(service, tmp_path, tags={"insiderrole": "worker"})

    for _ in range(2):
        response = await client.put(
            f"/api/projects/{first['id']}/conversations/{worker.hex}"
        )
        assert response.status_code == 204
    moved = await client.put(f"/api/projects/{second['id']}/conversations/{worker.hex}")

    assert moved.status_code == 204
    assert await _tags(service, worker) == {
        "insiderrole": "worker",
        "project": second["id"],
    }
    first_conversations = await client.get(f"/api/projects/{first['id']}/conversations")
    assert first_conversations.json()["conversations"] == []


@pytest.mark.asyncio
async def test_add_missing_conversation_is_404(client):
    project = (await client.post("/api/projects", json={"name": "P"})).json()

    response = await client.put(
        f"/api/projects/{project['id']}/conversations/{uuid4().hex}"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found"


@pytest.mark.asyncio
async def test_remove_conversation_clears_only_the_project_tag(
    client, service, tmp_path
):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    worker = await _start(service, tmp_path, tags={"insiderrole": "worker"})
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")

    removed = await client.delete(
        f"/api/projects/{project['id']}/conversations/{worker.hex}"
    )
    again = await client.delete(
        f"/api/projects/{project['id']}/conversations/{worker.hex}"
    )

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
    await client.put(f"/api/projects/{second['id']}/conversations/{worker.hex}")

    response = await client.delete(
        f"/api/projects/{first['id']}/conversations/{worker.hex}"
    )

    assert response.status_code == 404
    assert (await _tags(service, worker))["project"] == second["id"]


@pytest.mark.asyncio
async def test_coordinator_cannot_be_removed_from_its_project(
    client, service, tmp_path
):
    cat = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()

    response = await client.delete(
        f"/api/projects/{project['id']}/conversations/{cat.hex}"
    )

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


# Messages


def _send(text: str, sender: UUID | str | None = None, **extra) -> dict:
    """The body the coordinator's message tool sends to the generic route."""
    body = {
        "role": "user",
        "content": [{"type": "text", "text": text}],
        "run": True,
        "refuse_pending_confirmation": True,
        **extra,
    }
    if sender is not None:
        body["sender_conversation_id"] = str(sender)
    return body


async def _user_messages(service: ConversationService, conversation_id: UUID) -> int:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    state = await event_service.get_state()
    return sum(
        1
        for event in state.events
        if isinstance(event, MessageEvent) and event.source == "user"
    )


@pytest.mark.asyncio
async def test_message_refuses_conversation_waiting_for_confirmation(
    client, service, tmp_path
):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    worker = await _start(service, tmp_path)
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    await _set_status(
        service, worker, ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )

    response = await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("go ahead"),
    )

    assert response.status_code == 409
    assert await _user_messages(service, worker) == 0
    event_service = await service.get_event_service(worker)
    assert event_service is not None
    state = await event_service.get_state()
    assert (
        state.execution_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )


@pytest.mark.asyncio
async def test_project_sender_only_reaches_its_project(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cat = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    await client.put(f"/api/projects/{project['id']}/conversations/{cat.hex}")

    refused = await client.post(
        f"/api/conversations/{outsider.hex}/events", json=_send("hi", cat.hex)
    )
    missing = await client.post(
        f"/api/conversations/{uuid4().hex}/events", json=_send("hi", cat.hex)
    )

    assert refused.status_code == 422
    assert missing.status_code == 404
    assert await _user_messages(service, outsider) == 0


@pytest.mark.asyncio
async def test_sender_outside_any_project_is_named_as_a_conversation(
    client, service, tmp_path, monkeypatch
):
    sender = await _start(service, tmp_path)
    target = await _start(service, tmp_path)
    _save_without_running(monkeypatch)

    response = await client.post(
        f"/api/conversations/{target.hex}/events", json=_send("hi", sender.hex)
    )

    assert response.status_code == 200
    event = await _last_user_message(service, target)
    assert event.sender == f"conversation:{sender.hex}"
    header = event.llm_message.content[0].text  # type: ignore[union-attr]
    assert "another conversation, not by the person" in header


@pytest.mark.asyncio
async def test_message_runs_member_with_guard(client, service, tmp_path, monkeypatch):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    worker = await _start(service, tmp_path)
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    calls: list[dict] = []

    async def fake_send(self, message, run=False, **kwargs):
        calls.append({"text": message.content[0].text, "run": run, **kwargs})
        return "started"

    monkeypatch.setattr(EventService, "send_message", fake_send)

    response = await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("Also add docs"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "previous_status": "idle",
        "run": "started",
    }
    assert calls == [
        {
            "text": "Also add docs",
            "run": True,
            "refuse_pending_confirmation": True,
            "sender": None,
        }
    ]


def _save_without_running(monkeypatch) -> None:
    original = EventService.send_message

    async def save_only(self, message, run=False, **kwargs):
        kwargs.pop("refuse_pending_confirmation", None)
        await original(self, message, False, **kwargs)
        return "started"

    monkeypatch.setattr(EventService, "send_message", save_only)


async def _last_user_message(
    service: ConversationService, conversation_id: UUID
) -> MessageEvent:
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    state = await event_service.get_state()
    return [
        e for e in state.events if isinstance(e, MessageEvent) and e.source == "user"
    ][-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("from_coordinator", [True, False])
async def test_message_records_its_sender(
    client, service, tmp_path, monkeypatch, from_coordinator
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects",
            json={
                "name": "P",
                "coordinator_conversation_id": cat.hex if from_coordinator else None,
            },
        )
    ).json()
    for member in (cat, worker):
        await client.put(f"/api/projects/{project['id']}/conversations/{member.hex}")
    _save_without_running(monkeypatch)

    response = await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("Also add docs", cat.hex),
    )

    assert response.status_code == 200
    event = await _last_user_message(service, worker)
    assert event.sender == f"conversation:{cat.hex}"
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert text == "Also add docs"
    assert cat.hex in header and "not by the person" in header
    role = "the coordinator of your project"
    assert (role in header) is from_coordinator


@pytest.mark.asyncio
async def test_message_refuses_a_sender_outside_the_project(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    other = (await client.post("/api/projects", json={"name": "Q"})).json()
    worker = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    await client.put(f"/api/projects/{other['id']}/conversations/{outsider.hex}")

    for sender in (outsider.hex, uuid4().hex, worker.hex):
        response = await client.post(
            f"/api/conversations/{worker.hex}/events",
            json=_send("hi", sender),
        )
        assert response.status_code == 422
    assert await _user_messages(service, worker) == 0


@pytest.mark.asyncio
async def test_guarded_send_keeps_message_and_does_not_approve(service, tmp_path):
    """The race the endpoint guards against: the conversation is already waiting
    when the guarded send runs (the endpoint's own pre-check saw it idle)."""
    worker = await _start(service, tmp_path)
    await _set_status(
        service, worker, ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    event_service = await service.get_event_service(worker)
    assert event_service is not None

    with pytest.raises(ValueError, match="waiting_for_confirmation"):
        await event_service.send_message(
            Message(role="user", content=[TextContent(text="go ahead")]),
            run=True,
            refuse_pending_confirmation=True,
        )

    assert await _user_messages(service, worker) == 1
    state = await event_service.get_state()
    assert (
        state.execution_status == ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
    )
    assert event_service._run_task is None


@pytest.mark.asyncio
async def test_send_reports_queued_while_a_run_is_active(service, tmp_path):
    worker = await _start(service, tmp_path)
    event_service = await service.get_event_service(worker)
    assert event_service is not None
    active = asyncio.get_running_loop().create_future()
    event_service._run_task = active  # a run that has not ended yet
    try:
        outcome = await event_service.send_message(
            Message(role="user", content=[TextContent(text="also")]),
            run=True,
            refuse_pending_confirmation=True,
        )
    finally:
        event_service._run_task = None
        active.cancel()
        event_service._rerun_requested = False

    assert outcome == "queued"
    assert await _user_messages(service, worker) == 1


@pytest.mark.asyncio
async def test_send_without_run_returns_none(service, tmp_path):
    worker = await _start(service, tmp_path)
    event_service = await service.get_event_service(worker)
    assert event_service is not None

    outcome = await event_service.send_message(
        Message(role="user", content=[TextContent(text="note")]), run=False
    )

    assert outcome is None


@pytest.mark.asyncio
async def test_failed_coordinator_tag_undoes_the_whole_patch(
    client, service, tmp_path, monkeypatch
):
    old = await _start(service, tmp_path)
    new = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": old.hex}
        )
    ).json()

    async def vanished(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "set_conversation_tag", vanished)
    response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"name": "Q", "coordinator_conversation_id": new.hex},
    )

    assert response.status_code == 404
    current = (await client.get(f"/api/projects/{project['id']}")).json()
    assert current["name"] == "P"
    assert current["coordinator_conversation_id"] == old.hex


@pytest.mark.asyncio
async def test_resolved_and_turn_tags_refine_attention(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cases = {
        # title: (status, tags)
        "question": (
            ConversationExecutionStatus.FINISHED,
            {"turn": "asked", "ask": "Which database?"},
        ),
        "answer": (
            ConversationExecutionStatus.FINISHED,
            {"turn": "answered", "ask": "Stale?"},
        ),
        "done": (
            ConversationExecutionStatus.FINISHED,
            {"turn": "answered", "resolved": "auto"},
        ),
        "closed-running": (ConversationExecutionStatus.RUNNING, {"resolved": "user"}),
        "blocked": (
            ConversationExecutionStatus.WAITING_FOR_CONFIRMATION,
            {"resolved": "user"},
        ),
        "nightly": (
            ConversationExecutionStatus.IDLE,
            {"automationid": "auto-7", "turn": "bogus"},
        ),
    }
    for title, (status, tags) in cases.items():
        cid = await _start(
            service, tmp_path, title=title, tags={"project": project["id"], **tags}
        )
        await _set_status(service, cid, status)

    conversations = (
        await client.get(f"/api/projects/{project['id']}/conversations")
    ).json()["conversations"]

    by_title = {t["title"]: t for t in conversations}
    assert {t: by_title[t]["attention"] for t in cases} == {
        "question": "needs_input",
        "answer": "finished",
        "done": "resolved",
        "closed-running": "working",
        "blocked": "needs_you",
        "nightly": "idle",
    }
    assert by_title["done"]["resolved"] == "auto"
    assert by_title["question"]["turn"] == "asked"
    assert by_title["question"]["ask"] == "Which database?"
    assert by_title["answer"]["ask"] is None  # only while asked
    assert by_title["nightly"]["turn"] is None  # unknown values are ignored
    assert by_title["nightly"]["automation_id"] == "auto-7"
    assert [t["attention"] for t in conversations] == sorted(
        (t["attention"] for t in conversations),
        key=[
            "needs_you",
            "needs_input",
            "working",
            "finished",
            "idle",
            "resolved",
        ].index,
    )


# Artifacts


@pytest.mark.asyncio
async def test_upload_list_download_delete_artifact(client, persistence_dir):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/artifacts"

    uploaded = await client.post(
        base, files={"file": ("notes.md", b"# Notes\n", "text/markdown")}
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["source"] == "user"
    assert uploaded.json()["size"] == 8

    listed = (await client.get(base)).json()["artifacts"]
    assert [(a["name"], a["source"]) for a in listed] == [("notes.md", "user")]

    downloaded = await client.get(f"{base}/notes.md")
    assert downloaded.status_code == 200
    assert downloaded.content == b"# Notes\n"
    assert downloaded.headers["content-type"].startswith("text/markdown")

    assert (await client.delete(f"{base}/notes.md")).status_code == 200
    assert (await client.get(base)).json()["artifacts"] == []
    assert (await client.delete(f"{base}/notes.md")).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [".hidden", "../escape", "a/b.txt", "", "notes.md\n", "trailing.", "NUL.txt"],
)
async def test_upload_refuses_unsafe_names(client, name):
    project = (await client.post("/api/projects", json={"name": "P"})).json()

    response = await client.post(
        f"/api/projects/{project['id']}/artifacts",
        files={"file": ("ok.txt", b"x")},
        data={"name": name} if name else {"name": ".."},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_save_from_member_workspace(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    member = await _start(service, tmp_path, tags={"project": project["id"]})
    outsider = await _start(service, tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir()
    (workspace / "out" / "report.md").write_text("done")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope")
    (workspace / "link.txt").symlink_to(secret)
    url = f"/api/projects/{project['id']}/artifacts/from-conversation"

    saved = await client.post(
        url, json={"conversation_id": member.hex, "path": "out/report.md"}
    )
    escaped = await client.post(
        url, json={"conversation_id": member.hex, "path": "../secret.txt"}
    )
    via_link = await client.post(
        url, json={"conversation_id": member.hex, "path": "link.txt"}
    )
    not_member = await client.post(
        url, json={"conversation_id": outsider.hex, "path": "out/report.md"}
    )

    assert saved.status_code == 201
    assert saved.json()["source"] == "agent"
    assert saved.json()["conversation_id"] == member.hex
    assert escaped.status_code == 400
    assert via_link.status_code == 400
    assert not_member.status_code == 404
    names = [
        a["name"]
        for a in (await client.get(f"/api/projects/{project['id']}/artifacts")).json()[
            "artifacts"
        ]
    ]
    assert names == ["report.md"]


@pytest.mark.asyncio
async def test_deleting_a_project_deletes_its_artifacts(client, persistence_dir):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    await client.post(
        f"/api/projects/{project['id']}/artifacts", files={"file": ("a.txt", b"x")}
    )
    project_dir = persistence_dir / "projects" / project["id"]
    assert (project_dir / "artifacts" / "a.txt").read_bytes() == b"x"

    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 200

    assert not project_dir.exists()
    assert (
        await client.get(f"/api/projects/{project['id']}/artifacts")
    ).status_code == 404


@pytest.mark.asyncio
async def test_upload_over_the_limit_is_refused(client, monkeypatch):
    from openhands.agent_server import project_artifacts

    monkeypatch.setattr(project_artifacts, "MAX_ARTIFACT_BYTES", 4)
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/artifacts"

    response = await client.post(base, files={"file": ("big.bin", b"12345")})

    assert response.status_code == 413
    assert (await client.get(base)).json()["artifacts"] == []


@pytest.mark.asyncio
async def test_names_differing_only_in_case_are_refused(client):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/artifacts"

    first = await client.post(base, files={"file": ("Report.md", b"a")})
    second = await client.post(base, files={"file": ("report.md", b"b")})

    assert first.status_code == 201
    assert second.status_code == 409
    assert [a["name"] for a in (await client.get(base)).json()["artifacts"]] == [
        "Report.md"
    ]


@pytest.mark.asyncio
async def test_save_refuses_a_fifo_without_blocking(client, service, tmp_path):
    import os

    project = (await client.post("/api/projects", json={"name": "P"})).json()
    member = await _start(service, tmp_path, tags={"project": project["id"]})
    os.mkfifo(tmp_path / "workspace" / "pipe")

    response = await asyncio.wait_for(
        client.post(
            f"/api/projects/{project['id']}/artifacts/from-conversation",
            json={"conversation_id": member.hex, "path": "pipe"},
        ),
        timeout=5,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Path is not a file"


@pytest.mark.asyncio
async def test_resolved_running_conversation_is_working(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cid = await _start(
        service, tmp_path, tags={"project": project["id"], "resolved": "user"}
    )
    await _set_status(service, cid, ConversationExecutionStatus.RUNNING)

    conversations = (
        await client.get(f"/api/projects/{project['id']}/conversations")
    ).json()

    assert conversations["conversations"][0]["attention"] == "working"
    assert conversations["conversations"][0]["resolved"] == "user"


@pytest.mark.asyncio
async def test_new_tags_are_read_before_the_state_is_saved(
    service, tmp_path, monkeypatch
):
    # A run that holds the state lock (e.g. while an LLM call retries) delays
    # the state's save; the tags in meta.json must be visible meanwhile.
    from openhands.agent_server import conversation_service as module

    cid = await _start(service, tmp_path, tags={"project": "p1"})
    monkeypatch.setattr(module, "_update_state_tags_sync", lambda state, tags: state)

    await service.update_conversation(
        cid, UpdateConversationRequest(tags={"project": "p1", "resolved": "user"})
    )

    assert await _tags(service, cid) == {"project": "p1", "resolved": "user"}


# Coordinator updates and transcripts


@pytest.mark.asyncio
async def test_a_finished_member_wakes_its_coordinator(
    client, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    _save_without_running(monkeypatch)

    sent = await notify_coordinator(
        service,
        worker,
        project["id"],
        "asked",
        "Which database should I use?",
        f"conversation:{cat.hex}",
    )

    assert sent
    event = await _last_user_message(service, cat)
    assert event.sender == f"conversation:{worker.hex}"
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert "with a question for the person" in header and worker.hex in header
    assert text == "Which database should I use?"


@pytest.mark.asyncio
async def test_no_update_without_a_coordinator_or_to_itself(
    client, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    bare = (await client.post("/api/projects", json={"name": "Q"})).json()
    _save_without_running(monkeypatch)

    by_cat = f"conversation:{cat.hex}"
    assert not await notify_coordinator(
        service, cat, project["id"], "answered", "x", by_cat
    )
    assert not await notify_coordinator(
        service, cat, bare["id"], "answered", "x", by_cat
    )
    assert await _user_messages(service, cat) == 0


@pytest.mark.asyncio
async def test_only_the_coordinators_turns_wake_it(
    client, service, tmp_path, monkeypatch
):
    """The coordinator runs on replies to its own messages. A turn the person
    started is passed on without a run, and a turn another member started (or
    an update the coordinator answered) is not passed on at all."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    peer = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    for member in (worker, peer):
        await client.put(f"/api/projects/{project['id']}/conversations/{member.hex}")
    runs = _record_runs(monkeypatch)

    for sender in (f"conversation:{peer.hex}", "someone"):
        assert not await notify_coordinator(
            service, worker, project["id"], "answered", "x", sender
        )
    assert await _user_messages(service, cat) == 0

    assert await notify_coordinator(
        service, worker, project["id"], "asked", "Which colour?", None
    )
    event = await _last_user_message(service, cat)
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Reply in ") and "awareness" in header
    assert text == "Which colour?"
    assert event.sender == f"conversation:{worker.hex}"
    assert runs == [False]


def _record_runs(monkeypatch) -> list[bool]:
    """Save messages without running, recording whether a run was asked for."""
    original = EventService.send_message
    runs: list[bool] = []

    async def save_only(self, message, run=False, **kwargs):
        runs.append(run)
        kwargs.pop("refuse_pending_confirmation", None)
        await original(self, message, False, **kwargs)
        return "started" if run else None

    monkeypatch.setattr(EventService, "send_message", save_only)
    return runs


@pytest.mark.asyncio
async def test_the_persons_message_in_a_member_reaches_the_coordinator(
    client, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    runs = _record_runs(monkeypatch)

    assert await relay_person_message(service, worker, project["id"], "Use Postgres")
    assert not await relay_person_message(service, cat, project["id"], "Hi Cat")

    event = await _last_user_message(service, cat)
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Message from the person in ")
    assert worker.hex in header and "do not forward or repeat it" in header
    assert text == "Use Postgres"
    assert event.sender == f"conversation:{worker.hex}"
    assert runs == [False]


@pytest.mark.asyncio
async def test_transcript_lists_messages_with_their_senders(
    client, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "coordinator_conversation_id": cat.hex}
        )
    ).json()
    await client.put(f"/api/projects/{project['id']}/conversations/{worker.hex}")
    _save_without_running(monkeypatch)
    await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("Fix the bug", cat.hex),
    )
    await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("And add a test"),
    )

    body = (
        await client.get(
            f"/api/projects/{project['id']}/conversations/{worker.hex}/messages",
            params={"limit": 1},
        )
    ).json()
    full = (
        await client.get(
            f"/api/projects/{project['id']}/conversations/{worker.hex}/messages"
        )
    ).json()
    missing = await client.get(
        f"/api/projects/{project['id']}/conversations/{outsider.hex}/messages"
    )

    assert [m["text"] for m in body["messages"]] == ["And add a test"]
    assert [(m["sender"], m["source"]) for m in full["messages"]] == [
        (f"conversation:{cat.hex}", "user"),
        (None, "user"),
    ]
    assert full["messages"][0]["text"].endswith("Fix the bug")
    assert missing.status_code == 404


# Memory


@pytest.mark.asyncio
async def test_memory_write_read_list_and_delete(client, persistence_dir):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/memory"

    assert (await client.get(base)).json() == {"files": []}
    assert (await client.get(f"{base}/MEMORY.md")).status_code == 404

    index = await client.put(
        f"{base}/MEMORY.md", json={"content": "- Engel prefers squash merges.\n"}
    )
    topic = await client.put(f"{base}/deploy.md", json={"content": "Use staging."})
    assert index.status_code == topic.status_code == 200

    read = (await client.get(f"{base}/MEMORY.md")).json()
    assert read["content"] == "- Engel prefers squash merges.\n"
    assert read["revision"] == index.json()["revision"]
    assert [f["name"] for f in (await client.get(base)).json()["files"]] == [
        "MEMORY.md",
        "deploy.md",
    ]
    assert (
        persistence_dir / "projects" / project["id"] / "memory" / "deploy.md"
    ).read_text() == "Use staging."

    updated = await client.put(
        f"{base}/MEMORY.md",
        json={"content": "- Squash merges.\n", "base_revision": read["revision"]},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] != read["revision"]

    deleted = await client.delete(
        f"{base}/deploy.md", params={"base_revision": topic.json()["revision"]}
    )
    assert deleted.json() == {"deleted": True}
    assert (await client.delete(f"{base}/deploy.md")).status_code == 404


@pytest.mark.asyncio
async def test_a_stale_memory_write_is_refused_with_the_current_file(client):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    path = f"/api/projects/{project['id']}/memory/MEMORY.md"
    first = (await client.put(path, json={"content": "one"})).json()
    await client.put(path, json={"content": "two", "base_revision": first["revision"]})

    stale = await client.put(
        path, json={"content": "three", "base_revision": first["revision"]}
    )
    recreate = await client.put(path, json={"content": "new"})
    stale_delete = await client.delete(
        path, params={"base_revision": first["revision"]}
    )

    for response in (stale, recreate, stale_delete):
        assert response.status_code == 409
        assert response.json()["detail"]["current"]["content"] == "two"
    assert (await client.get(path)).json()["content"] == "two"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Notes.md", "notes.txt", "..md", ".hidden.md"])
async def test_memory_names_are_checked(client, name):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    response = await client.put(
        f"/api/projects/{project['id']}/memory/{name}", json={"content": "x"}
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_memory_files_are_kept_short(client):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    response = await client.put(
        f"/api/projects/{project['id']}/memory/MEMORY.md",
        json={"content": "x" * (64 * 1024 + 1)},
    )
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_deleting_a_project_deletes_its_memory(client, persistence_dir):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    await client.put(
        f"/api/projects/{project['id']}/memory/MEMORY.md", json={"content": "x"}
    )

    await client.delete(f"/api/projects/{project['id']}")

    assert not (persistence_dir / "projects" / project["id"]).exists()
    assert (
        await client.get(f"/api/projects/{project['id']}/memory")
    ).status_code == 404
