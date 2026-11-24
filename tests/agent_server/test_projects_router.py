"""Tests for the projects router.

Projects are stored in ``projects.json``; membership is the ``project`` tag on
each conversation. These tests drive the HTTP surface against a real
``ConversationService`` so tag changes go through the same persistence path
as ``PATCH /api/conversations/{id}``.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from openhands.agent_server.api import add_domain_error_handler
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_router import event_router
from openhands.agent_server.event_service import EventService
from openhands.agent_server.exceptions import ConversationRunLimitExceeded
from openhands.agent_server.models import (
    StartConversationRequest,
    UpdateConversationRequest,
)
from openhands.agent_server.persistence import (
    PersistedProjects,
    PersistedSettings,
    ProjectItem,
    ServerStores,
)
from openhands.agent_server.project_wake import (
    notify_coordinator,
    notify_coordinator_of_stop,
    reconcile_coordinator_updates,
    relay_user_message,
)
from openhands.agent_server.projects_router import projects_router
from openhands.sdk import LLM, Agent, Message, TextContent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.errors import Unprocessable
from openhands.sdk.event import MessageEvent
from openhands.sdk.profiles import ACPAgentProfile, OpenHandsAgentProfile
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.conftest import install_test_context, update_test_context


pytestmark = pytest.mark.projects_on


@pytest.fixture
def persistence_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    path = tmp_path / "persist"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(path))
    yield path


@pytest.fixture
def stores(persistence_dir) -> ServerStores:
    return ServerStores.from_config(Config())


@pytest.fixture
async def service(tmp_path, stores) -> AsyncIterator[ConversationService]:
    async with ConversationService(
        conversations_dir=tmp_path / "conversations", stores=stores
    ) as service:
        yield service


@pytest.fixture
async def client(service, stores) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    add_domain_error_handler(app)
    install_test_context(
        app,
        Config(static_files_path=None, session_api_keys=[], secret_key=None),
        conversation_service=service,
    )
    update_test_context(app, stores=stores)
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


async def _join(
    service: ConversationService, project_id: str, conversation_id: UUID
) -> None:
    """Make a conversation a member, as the coordinator's start does."""
    tags = await service.projects.set_conversation_tag(
        conversation_id, "project", project_id
    )
    assert tags is not None


async def _project_led_by(
    client: httpx.AsyncClient,
    stores: ServerStores,
    service: ConversationService,
    coordinator: UUID,
) -> dict:
    """A project led by ``coordinator``, as starting a new coordinator leaves it."""
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    await _join(service, project["id"], coordinator)

    def lead(current: PersistedProjects) -> PersistedProjects:
        projects = [
            p.model_copy(update={"coordinator_conversation_id": coordinator})
            if p.id.hex == project["id"]
            else p
            for p in current.projects
        ]
        return current.model_copy(update={"projects": projects})

    stores.projects.update(lead)
    return {**project, "coordinator_conversation_id": coordinator.hex}


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
async def test_unknown_project_is_404_everywhere(client):
    missing = uuid4().hex

    assert (await client.get(f"/api/projects/{missing}")).status_code == 404
    assert (
        await client.patch(f"/api/projects/{missing}", json={"name": "x"})
    ).status_code == 404
    assert (await client.delete(f"/api/projects/{missing}")).status_code == 404
    assert (
        await client.get(f"/api/projects/{missing}/conversations")
    ).status_code == 404


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
    conversation_id = await _start(service, tmp_path, tags={"env": "dev"})
    await _join(service, project["id"], conversation_id)
    await service.projects.set_conversation_tag(conversation_id, "turn", "asking")

    response = await client.delete(f"/api/projects/{project['id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert (await client.get("/api/projects")).json() == {"projects": []}
    assert await service.get_conversation(conversation_id) is not None
    # The project's tags go with it; the conversation's own stay.
    assert await _tags(service, conversation_id) == {"env": "dev"}


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
async def test_a_docker_coordinator_is_refused_before_the_project_is_saved(
    client, persistence_dir, tmp_path
):
    response = await client.post(
        "/api/projects",
        json={
            "name": "P",
            "coordinator": {
                "working_dir": str(tmp_path),
                "conversation_runtime": "docker",
            },
        },
    )

    assert response.status_code == 422
    assert "only runs local conversations" in str(response.json()["detail"])
    assert not (persistence_dir / "projects.json").exists()


@pytest.mark.asyncio
async def test_an_acp_coordinator_is_refused_before_the_project_is_saved(
    client, stores, persistence_dir, tmp_path
):
    acp = ACPAgentProfile(name="acp", acp_server="claude-code")
    stores.agent_profiles.save(acp)
    stores.settings.save(PersistedSettings(active_agent_profile_id=str(acp.id)))

    named = await client.post(
        "/api/projects",
        json={
            "name": "P",
            "coordinator": {
                "working_dir": str(tmp_path),
                "agent_profile_id": acp.id.hex,
            },
        },
    )
    active = await client.post(
        "/api/projects",
        json={"name": "P", "coordinator": {"working_dir": str(tmp_path)}},
    )

    assert named.status_code == 422
    assert active.status_code == 422
    assert "ACP agent" in active.json()["detail"]
    assert (await client.get("/api/projects")).json()["projects"] == []


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
        await _join(service, project["id"], ids[title])
    outsider = await _start(service, tmp_path, title="outsider")

    response = await client.get(f"/api/projects/{project['id']}/conversations")

    assert response.status_code == 200
    conversations = response.json()["conversations"]
    # Within a group, newest-updated first: "broken" was added after "asks".
    assert [(t["title"], t["attention"]) for t in conversations] == [
        ("broken", "needs_you"),
        ("asks", "needs_you"),
        ("busy", "working"),
        ("done", "idle"),
        ("idle", "idle"),
    ]
    assert outsider.hex not in {t["conversation_id"] for t in conversations}
    assert conversations[0]["execution_status"] == "error"


@pytest.mark.asyncio
async def test_tagged_children_are_conversations_with_their_parent(
    client, stores, service, tmp_path
):
    cat = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
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
async def test_remove_conversation_clears_the_project_and_board_tags(
    client, service, tmp_path
):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    worker = await _start(service, tmp_path, tags={"insiderrole": "worker"})
    await _join(service, project["id"], worker)
    await service.projects.set_conversation_tag(worker, "turn", "asking")
    await service.projects.set_conversation_tag(worker, "ask", "Ship it?")

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
    await _join(service, second["id"], worker)

    response = await client.delete(
        f"/api/projects/{first['id']}/conversations/{worker.hex}"
    )

    assert response.status_code == 404
    assert (await _tags(service, worker))["project"] == second["id"]


@pytest.mark.asyncio
async def test_coordinator_cannot_be_removed_from_its_project(
    client, stores, service, tmp_path
):
    cat = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)

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
        await service.projects.set_conversation_tag(worker, "project", "abc")

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
async def test_project_sender_only_reaches_its_project(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cat = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    await _join(service, project["id"], cat)

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
    assert "another conversation, not by the user" in header


async def _tagged_sender_reaches_untagged(client, service, tmp_path, monkeypatch):
    sender = await _start(service, tmp_path)
    await service.projects.set_conversation_tag(sender, "project", "billing")
    target = await _start(service, tmp_path)
    _save_without_running(monkeypatch)

    response = await client.post(
        f"/api/conversations/{target.hex}/events", json=_send("hi", sender.hex)
    )

    assert response.status_code == 200, response.text
    event = await _last_user_message(service, target)
    assert event.sender == f"conversation:{sender.hex}"
    header = event.llm_message.content[0].text  # type: ignore[union-attr]
    assert "another conversation, not by the user" in header


@pytest.mark.asyncio
async def test_a_project_tag_naming_no_project_is_ordinary_for_senders(
    client, service, tmp_path, monkeypatch
):
    """A client's own ``project`` tag does not limit whom it can message."""
    await _tagged_sender_reaches_untagged(client, service, tmp_path, monkeypatch)


@pytest.mark.asyncio
@pytest.mark.projects_off
async def test_with_projects_off_a_project_tag_does_not_limit_senders(
    client, service, tmp_path, monkeypatch
):
    await _tagged_sender_reaches_untagged(client, service, tmp_path, monkeypatch)


def _save_without_running(monkeypatch) -> None:
    original = EventService.send_message

    async def save_only(self, message, run=False, **kwargs):
        await original(self, message, False, **kwargs)

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
    client, stores, service, tmp_path, monkeypatch, from_coordinator
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = (
        await _project_led_by(client, stores, service, cat)
        if from_coordinator
        else (await client.post("/api/projects", json={"name": "P"})).json()
    )
    for member in (cat, worker):
        await _join(service, project["id"], member)
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
    assert cat.hex in header and "not by the user" in header
    role = "the coordinator of your project"
    assert (role in header) is from_coordinator


@pytest.mark.asyncio
async def test_message_refuses_a_sender_outside_the_project(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    other = (await client.post("/api/projects", json={"name": "Q"})).json()
    worker = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    await _join(service, project["id"], worker)
    await _join(service, other["id"], outsider)

    for sender in (outsider.hex, uuid4().hex, worker.hex):
        response = await client.post(
            f"/api/conversations/{worker.hex}/events",
            json=_send("hi", sender),
        )
        assert response.status_code == 422
    assert await _user_messages(service, worker) == 0


@pytest.mark.asyncio
async def test_send_without_run_returns_none(service, tmp_path):
    worker = await _start(service, tmp_path)
    event_service = await service.get_event_service(worker)
    assert event_service is not None

    outcome = await event_service.send_message(
        Message(role="user", content=[TextContent(text="note")]), run=False
    )

    assert outcome is None


def test_sender_fields_are_left_out_of_a_plain_message():
    """The sender fields live on SendMessageRequest, but a user's own message
    serializes exactly as before they existed."""
    from openhands.sdk.conversation.message_request import SendMessageRequest

    plain = SendMessageRequest(content=[TextContent(text="hi")])
    assert "sender_conversation_id" not in plain.model_dump()
    sent = SendMessageRequest(sender_conversation_id=uuid4()).model_dump(mode="json")
    assert "sender_conversation_id" in sent


@pytest.mark.asyncio
async def test_resolved_and_turn_tags_refine_attention(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    cases = {
        # title: (status, tags)
        "question": (
            ConversationExecutionStatus.FINISHED,
            {"turn": "asking", "ask": "Which database?"},
        ),
        "answer": (
            ConversationExecutionStatus.FINISHED,
            {
                "turn": "finished",
                "ask": "Stale?",
                "prs": "acme/app#412,not a ref,acme/lib#9",
            },
        ),
        "done": (
            ConversationExecutionStatus.FINISHED,
            {"turn": "finished", "resolved": "auto"},
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
        "question": "asking",
        "answer": "idle",
        "done": "resolved",
        "closed-running": "working",
        "blocked": "needs_you",
        "nightly": "idle",
    }
    assert by_title["done"]["resolved"] == "auto"
    assert by_title["question"]["turn"] == "asking"
    assert by_title["question"]["ask"] == "Which database?"
    assert by_title["answer"]["ask"] is None  # only while asked
    assert by_title["answer"]["pull_requests"] == [  # malformed refs are skipped
        {"owner": "acme", "repo": "app", "number": 412},
        {"owner": "acme", "repo": "lib", "number": 9},
    ]
    assert by_title["done"]["pull_requests"] == []
    assert by_title["nightly"]["turn"] is None  # unknown values are ignored
    assert by_title["nightly"]["automation_id"] == "auto-7"
    assert [t["attention"] for t in conversations] == sorted(
        (t["attention"] for t in conversations),
        key=[
            "needs_you",
            "asking",
            "working",
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
    # Sent like a workspace file download: saved, never rendered or sniffed.
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"] == (
        'attachment; filename="notes.md"'
    )
    assert downloaded.headers["x-content-type-options"] == "nosniff"

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
    client, stores, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    _save_without_running(monkeypatch)

    sent = await notify_coordinator(
        service,
        worker,
        project["id"],
        "asking",
        "Which database should I use?",
        f"conversation:{cat.hex}",
    )

    assert sent
    event = await _last_user_message(service, cat)
    assert event.sender == f"conversation:{worker.hex}"
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert "with a question for the user" in header and worker.hex in header
    assert text == "Which database should I use?"


@pytest.mark.parametrize(
    ("status", "runs"),
    [
        (ConversationExecutionStatus.WAITING_FOR_CONFIRMATION, False),
        (ConversationExecutionStatus.IDLE, True),
    ],
)
@pytest.mark.asyncio
async def test_an_update_never_approves_the_coordinators_pending_actions(
    client, stores, service, tmp_path, monkeypatch, status, runs
):
    """A run would approve them, so the update is saved for the next run."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    await _set_status(service, cat, status)
    asked: list[bool] = []
    original = EventService.send_message

    async def record(self, message, run=False, **kwargs):
        asked.append(run)
        await original(self, message, False, **kwargs)

    monkeypatch.setattr(EventService, "send_message", record)

    assert await notify_coordinator(
        service, worker, project["id"], "finished", "Done.", f"conversation:{cat.hex}"
    )

    assert asked == [runs]
    assert (await _last_user_message(service, cat)).sender == (
        f"conversation:{worker.hex}"
    )


@pytest.mark.asyncio
async def test_no_update_without_a_coordinator_or_to_itself(
    client, stores, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    bare = (await client.post("/api/projects", json={"name": "Q"})).json()
    _save_without_running(monkeypatch)

    by_cat = f"conversation:{cat.hex}"
    assert not await notify_coordinator(
        service, cat, project["id"], "finished", "x", by_cat
    )
    assert not await notify_coordinator(
        service, cat, bare["id"], "finished", "x", by_cat
    )
    assert await _user_messages(service, cat) == 0


@pytest.mark.asyncio
async def test_only_the_coordinators_turns_wake_it(
    client, stores, service, tmp_path, monkeypatch
):
    """The coordinator runs on replies to its own messages. A turn the user
    started is passed on without a run, and a turn another member started (or
    an update the coordinator answered) is not passed on at all."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    peer = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    for member in (worker, peer):
        await _join(service, project["id"], member)
    runs = _record_runs(monkeypatch)

    for sender in (f"conversation:{peer.hex}", "someone"):
        assert not await notify_coordinator(
            service, worker, project["id"], "finished", "x", sender
        )
    assert await _user_messages(service, cat) == 0

    assert await notify_coordinator(
        service, worker, project["id"], "asking", "Which colour?", None
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
        await original(self, message, False, **kwargs)
        return "started" if run else None

    monkeypatch.setattr(EventService, "send_message", save_only)
    return runs


@pytest.mark.asyncio
async def test_the_users_message_in_a_member_reaches_the_coordinator(
    client, stores, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    runs = _record_runs(monkeypatch)

    assert await relay_user_message(service, worker, project["id"], "Use Postgres")
    assert not await relay_user_message(service, cat, project["id"], "Hi Cat")

    event = await _last_user_message(service, cat)
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Message from the user in ")
    assert worker.hex in header and "do not forward or repeat it" in header
    assert text == "Use Postgres"
    assert event.sender == f"conversation:{worker.hex}"
    assert runs == [False]


@pytest.mark.asyncio
async def test_transcript_lists_messages_with_their_senders(
    client, stores, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    outsider = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    _save_without_running(monkeypatch)
    await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("Fix the bug", cat.hex),
    )
    await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("And add a test"),
    )

    # The transcript reads back from the end only until it has its messages.
    with patch(
        "openhands.sdk.conversation.state.ConversationState.active_branch",
        side_effect=AssertionError("read the whole branch"),
    ):
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
@pytest.mark.parametrize(
    # memory.md would be the index on a case-insensitive filesystem.
    "name",
    ["Notes.md", "notes.txt", "..md", ".hidden.md", "memory.md"],
)
async def test_memory_names_are_checked(client, name):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    response = await client.put(
        f"/api/projects/{project['id']}/memory/{name}", json={"content": "x"}
    )
    assert response.status_code == 400


def test_project_files_are_locked_across_processes(tmp_path):
    """Review: two server processes sharing a persistence folder take turns
    on a project's memory and artifacts, as they do on projects.json."""
    import subprocess
    import sys
    import threading

    from openhands.agent_server.project_memory import ProjectMemoryStore

    project_id = uuid4()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, uuid\n"
            "from pathlib import Path\n"
            "from openhands.agent_server.project_artifacts import "
            "project_files_lock\n"
            f"with project_files_lock(Path({str(tmp_path)!r}), "
            f"uuid.UUID({project_id.hex!r})):\n"
            "    print('held', flush=True)\n"
            "    sys.stdin.readline()\n",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "OPENHANDS_SUPPRESS_BANNER": "1"},
    )
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline().strip() == "held"
        written = threading.Event()

        def write() -> None:
            ProjectMemoryStore(tmp_path).write(project_id, "MEMORY.md", "x", None)
            written.set()

        writer = threading.Thread(target=write)
        writer.start()
        assert not written.wait(0.5)
        holder.stdin.write("\n")
        holder.stdin.flush()
        assert written.wait(10)
        writer.join()
    finally:
        holder.kill()
        holder.wait()


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


@pytest.mark.asyncio
async def test_an_update_leaves_the_coordinators_goal_loop_running(
    client, stores, service, tmp_path, monkeypatch
):
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    _save_without_running(monkeypatch)
    stopped: list[UUID] = []

    async def stop_goal_loop(self):
        stopped.append(self.stored.id)

    monkeypatch.setattr(EventService, "stop_goal_loop", stop_goal_loop)

    assert await notify_coordinator(
        service, worker, project["id"], "finished", "Done.", f"conversation:{cat.hex}"
    )
    assert await relay_user_message(service, worker, project["id"], "Use Postgres")

    assert stopped == []


# Worker agent profile


@pytest.mark.asyncio
async def test_a_project_names_its_workers_agent_profile(client, stores):
    worker = OpenHandsAgentProfile(name="worker", llm_profile_ref="default")
    stores.agent_profiles.save(worker)
    known = worker.id
    acp = ACPAgentProfile(name="acp", acp_server="claude-code")
    stores.agent_profiles.save(acp)

    unknown = await client.post(
        "/api/projects", json={"name": "P", "worker_agent_profile_id": str(uuid4())}
    )
    refused = await client.post(
        "/api/projects", json={"name": "P", "worker_agent_profile_id": str(acp.id)}
    )
    project = (
        await client.post(
            "/api/projects", json={"name": "P", "worker_agent_profile_id": str(known)}
        )
    ).json()
    renamed = (
        await client.patch(f"/api/projects/{project['id']}", json={"name": "Q"})
    ).json()
    cleared = (
        await client.patch(
            f"/api/projects/{project['id']}", json={"worker_agent_profile_id": None}
        )
    ).json()

    assert unknown.status_code == 404
    assert refused.status_code == 422
    assert "ACP agent" in refused.json()["detail"]
    assert project["worker_agent_profile_id"] == known.hex
    assert renamed["worker_agent_profile_id"] == known.hex
    assert cleared["worker_agent_profile_id"] is None


# A new coordinator started with the project


@pytest.mark.asyncio
async def test_creating_a_project_can_start_its_coordinator(
    client, service, tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    started: list[tuple] = []

    async def start_coordinator(project_hex, working_dir, **kwargs):
        started.append((project_hex, working_dir, kwargs))
        coordinator = await _start(
            service, tmp_path, title="Cat", tags={"project": project_hex}
        )
        return SimpleNamespace(id=coordinator)

    monkeypatch.setattr(service.projects, "start_coordinator", start_coordinator)

    response = await client.post(
        "/api/projects",
        json={
            "name": "P",
            "coordinator": {
                "working_dir": str(workspace),
                "title": "Cat",
                "max_iterations": 42,
            },
        },
    )

    assert response.status_code == 201, response.text
    project = response.json()
    assert project["coordinator_conversation_id"] is not None
    ((project_hex, working_dir, kwargs),) = started
    assert project_hex == project["id"]
    assert working_dir == str(workspace)
    assert kwargs == {
        "agent_profile_id": None,
        "title": "Cat",
        "options": {"tags": {}, "max_iterations": 42},
    }
    listed = (await client.get(f"/api/projects/{project['id']}")).json()
    assert (
        listed["coordinator_conversation_id"] == project["coordinator_conversation_id"]
    )


@pytest.mark.asyncio
async def test_a_coordinator_that_cannot_start_leaves_no_project(
    client, service, tmp_path, monkeypatch
):
    async def start_coordinator(project_hex, working_dir, **kwargs):
        raise ValueError("No agent profile to start the coordinator from")

    monkeypatch.setattr(service.projects, "start_coordinator", start_coordinator)

    response = await client.post(
        "/api/projects",
        json={"name": "P", "coordinator": {"working_dir": str(tmp_path)}},
    )

    assert response.status_code == 422
    assert "No agent profile" in response.json()["detail"]
    assert (await client.get("/api/projects")).json()["projects"] == []


@pytest.mark.asyncio
async def test_a_member_stopped_on_the_user_wakes_its_coordinator(
    client, stores, service, tmp_path, monkeypatch
):
    """Review S1: the coordinator learns that a worker it started waits on the
    user, and runs to tell them; a pause is only noted."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    runs = _record_runs(monkeypatch)
    by_cat = f"conversation:{cat.hex}"

    assert await notify_coordinator_of_stop(
        service,
        worker,
        project["id"],
        "waiting_for_confirmation",
        "terminal: push the release tag",
        by_cat,
    )
    event = await _last_user_message(service, cat)
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Update from ") and "approve or reject" in header
    assert text == "terminal: push the release tag"

    assert await notify_coordinator_of_stop(
        service, worker, project["id"], "paused", "Halfway.", by_cat
    )
    event = await _last_user_message(service, cat)
    assert event.llm_message.content[0].text.startswith("[Paused by the user: ")  # type: ignore[union-attr]
    assert runs == [True, False]

    # The user's own turns, and stops that are not on the user, stay quiet.
    assert not await notify_coordinator_of_stop(
        service, worker, project["id"], "error", "boom", None
    )
    assert not await notify_coordinator_of_stop(
        service, worker, project["id"], "finished", "done", by_cat
    )
    assert await _user_messages(service, cat) == 2


@pytest.mark.asyncio
async def test_an_update_waits_for_a_run_slot_instead_of_being_dropped(
    client, stores, service, tmp_path, monkeypatch
):
    """Review S2: with every run slot taken, an update was saved but nothing
    ever ran the coordinator on it."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path)
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    original = EventService.send_message

    async def full(self, message, run=False, **kwargs):
        await original(self, message, False, **kwargs)
        if run:
            raise ConversationRunLimitExceeded("full")

    freed = asyncio.Event()
    ran: list[UUID] = []

    async def run_when_free(self):
        await freed.wait()
        ran.append(self.stored.id)
        return "started"

    monkeypatch.setattr(EventService, "send_message", full)
    monkeypatch.setattr(EventService, "run_saved_message_when_free", run_when_free)

    assert await notify_coordinator(
        service, worker, project["id"], "finished", "Done.", f"conversation:{cat.hex}"
    )
    await asyncio.sleep(0)
    assert ran == []
    freed.set()
    for _ in range(10):
        if ran:
            break
        await asyncio.sleep(0.01)

    assert ran == [cat]
    assert await _user_messages(service, cat) == 1


async def _agent_says(
    service: ConversationService,
    conversation_id: UUID,
    text: str,
    status: ConversationExecutionStatus,
) -> None:
    """Append the agent's last message and end the run, as if before a restart
    that left no subscriber to deliver it."""
    event_service = await service.get_event_service(conversation_id)
    assert event_service is not None
    state = await event_service.get_state()
    with state:
        state.events.append(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text=text)]),
            )
        )
        state.execution_status = status


@pytest.mark.asyncio
async def test_a_restart_delivers_the_updates_it_missed(
    client, stores, service, tmp_path, monkeypatch
):
    """Review S2/F3: a turn the coordinator started that ended while no update
    reached it (the server stopped first) is delivered on the next start,
    once; turns the user started are not replayed."""
    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    runs = _record_runs(monkeypatch)
    worker_service = await service.get_event_service(worker)
    assert worker_service is not None
    # As after a restart: no live subscriber saw these turns end.
    from openhands.agent_server.project_board import ConversationOutcomeSubscriber

    async def unseen(self, event) -> None:
        return None

    monkeypatch.setattr(ConversationOutcomeSubscriber, "__call__", unseen)

    await worker_service.send_message(
        Message(role="user", content=[TextContent(text="Pick a database.")]),
        sender=f"conversation:{cat.hex}",
    )
    await _agent_says(
        service,
        worker,
        "Postgres, for the joins.",
        ConversationExecutionStatus.FINISHED,
    )
    runs.clear()

    assert await reconcile_coordinator_updates(service) == 1
    event = await _last_user_message(service, cat)
    assert event.sender == f"conversation:{worker.hex}"
    header, text = (c.text for c in event.llm_message.content)  # type: ignore[union-attr]
    assert header.startswith("[Update from ") and worker.hex in header
    assert text == "Postgres, for the joins."
    assert runs == [True]

    # Delivered once: a second start finds nothing missing.
    assert await reconcile_coordinator_updates(service) == 0

    # A later coordinator turn that stopped on an error is reported as a stop.
    await worker_service.send_message(
        Message(role="user", content=[TextContent(text="Now migrate it.")]),
        sender=f"conversation:{cat.hex}",
    )
    await _agent_says(
        service, worker, "Migration failed.", ConversationExecutionStatus.ERROR
    )
    runs.clear()
    assert await reconcile_coordinator_updates(service) == 1
    header = (await _last_user_message(service, cat)).llm_message.content[0].text  # type: ignore[union-attr]
    assert header.startswith("[Update from ") and "error" in header
    assert runs == [True]

    # The user's own turn is theirs; it is not replayed to the coordinator.
    await worker_service.send_message(
        Message(role="user", content=[TextContent(text="Thanks, I'll take it.")])
    )
    await _agent_says(service, worker, "Sure.", ConversationExecutionStatus.FINISHED)
    copies = await _user_messages(service, cat)
    assert await reconcile_coordinator_updates(service) == 0
    assert await _user_messages(service, cat) == copies


@pytest.mark.asyncio
async def test_a_worker_stopped_by_a_shutdown_is_reported_after_the_restart(
    client, stores, service, tmp_path, monkeypatch
):
    """While the server goes down, nothing is sent; the catch-up after it
    starts again reports the worker in the status it came back in."""
    from openhands.agent_server.project_wake import notify_coordinator_of_stop

    cat = await _start(service, tmp_path)
    worker = await _start(service, tmp_path, title="worker")
    project = await _project_led_by(client, stores, service, cat)
    await _join(service, project["id"], worker)
    runs = _record_runs(monkeypatch)
    worker_service = await service.get_event_service(worker)
    assert worker_service is not None
    from openhands.agent_server.project_board import ConversationOutcomeSubscriber

    async def unseen(self, event) -> None:
        return None

    monkeypatch.setattr(ConversationOutcomeSubscriber, "__call__", unseen)
    await worker_service.send_message(
        Message(role="user", content=[TextContent(text="Run the suite.")]),
        sender=f"conversation:{cat.hex}",
    )
    await _agent_says(
        service, worker, "Running the tests now.", ConversationExecutionStatus.PAUSED
    )
    copies = await _user_messages(service, cat)
    runs.clear()

    worker_service._closing = True
    try:
        assert not await notify_coordinator_of_stop(
            service,
            worker,
            project["id"],
            "paused",
            "Running the tests now.",
            f"conversation:{cat.hex}",
        )
    finally:
        worker_service._closing = False
    assert await _user_messages(service, cat) == copies

    assert await reconcile_coordinator_updates(service) == 1
    header = (await _last_user_message(service, cat)).llm_message.content[0].text  # type: ignore[union-attr]
    assert header.startswith("[Paused by the user: ")
    assert runs == [False]


@pytest.mark.asyncio
async def test_the_server_catches_up_on_updates_when_it_starts(tmp_path, monkeypatch):
    from openhands.agent_server import project_service as module

    started = asyncio.Event()

    async def reconcile(service) -> int:
        started.set()
        return 0

    monkeypatch.setattr(module, "reconcile_coordinator_updates", reconcile)
    async with ConversationService(
        conversations_dir=tmp_path / "conversations",
        reconcile_projects_on_start=True,
    ):
        await asyncio.wait_for(started.wait(), timeout=5)


@pytest.mark.asyncio
async def test_resolve_and_reopen_routes(client, service, tmp_path):
    """Review S6/N2: single-purpose routes; both clear an auto-resolve note."""
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/conversations"
    member = await _start(
        service,
        tmp_path,
        tags={
            "project": project["id"],
            "turn": "finished",
            "resolved": "auto",
            "resolvenote": "tests pass",
        },
    )
    outsider = await _start(service, tmp_path)

    assert (await client.post(f"{base}/{member.hex}/resolve")).status_code == 204
    assert await _tags(service, member) == {
        "project": project["id"],
        "turn": "finished",
        "resolved": "user",
    }
    assert (await client.post(f"{base}/{member.hex}/resolve")).status_code == 204

    assert (await client.post(f"{base}/{member.hex}/reopen")).status_code == 204
    assert await _tags(service, member) == {
        "project": project["id"],
        "turn": "finished",
    }
    assert (await client.post(f"{base}/{member.hex}/reopen")).status_code == 204

    assert (await client.post(f"{base}/{outsider.hex}/resolve")).status_code == 404
    missing = f"/api/projects/{uuid4().hex}/conversations/{member.hex}/resolve"
    assert (await client.post(missing)).status_code == 404


@pytest.mark.asyncio
async def test_the_agent_cannot_resolve_over_the_users_open_question(
    client, service, tmp_path
):
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/conversations"
    tags = {"project": project["id"], "turn": "asking", "ask": "Which name?"}
    member = await _start(service, tmp_path, tags=tags)

    response = await client.post(f"{base}/{member.hex}/resolve", json={"by": "agent"})

    assert response.status_code == 409
    assert "theirs to answer" in response.json()["detail"]
    assert await _tags(service, member) == tags
    # The user can.
    assert (await client.post(f"{base}/{member.hex}/resolve")).status_code == 204


@pytest.mark.asyncio
async def test_resolve_by_the_agent_is_auto_and_never_over_the_user(
    client, service, tmp_path
):
    """resolve_conversation: the worker resolves its own conversation."""
    project = (await client.post("/api/projects", json={"name": "P"})).json()
    base = f"/api/projects/{project['id']}/conversations"
    member = await _start(
        service, tmp_path, tags={"project": project["id"], "turn": "finished"}
    )
    agent = {"json": {"by": "agent"}}

    assert (
        await client.post(f"{base}/{member.hex}/resolve", **agent)
    ).status_code == 202
    await asyncio.gather(*service.projects._agent_resolves)
    assert await _tags(service, member) == {
        "project": project["id"],
        "turn": "finished",
        "resolved": "auto",
        "resolvenote": "agent",
    }
    listed = (await client.get(base)).json()["conversations"]
    assert [(c["resolved"], c["resolve_note"]) for c in listed] == [("auto", "agent")]

    assert (await client.post(f"{base}/{member.hex}/resolve")).status_code == 204
    assert (
        await client.post(f"{base}/{member.hex}/resolve", **agent)
    ).status_code == 202
    await asyncio.gather(*service.projects._agent_resolves)
    assert (await _tags(service, member))["resolved"] == "user"
    assert "resolvenote" not in await _tags(service, member)
    bad = await client.post(f"{base}/{member.hex}/resolve", json={"by": "coordinator"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_a_client_patch_keeps_the_tags_the_server_owns(client, service, tmp_path):
    """Review S6/N2: a full-set PATCH built from tags read a moment ago cannot
    undo a board update, set board state or change membership. Resolving goes
    through the resolve and reopen routes only."""
    p1 = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    member = await _start(
        service,
        tmp_path,
        tags={"project": p1, "turn": "asking", "ask": "Which DB?", "env": "dev"},
    )

    # Stale read: no board tags, a different project, a forged resolve note.
    await service.projects.update_conversation_from_client(
        member,
        UpdateConversationRequest(
            tags={"project": "p2", "env": "prod", "resolvenote": "x", "turn": "y"}
        ),
    )
    assert await _tags(service, member) == {
        "project": p1,
        "turn": "asking",
        "ask": "Which DB?",
        "env": "prod",
    }

    # Writing resolved does nothing, either way.
    current = await _tags(service, member)
    for value in ("user", "auto"):
        await service.projects.update_conversation_from_client(
            member, UpdateConversationRequest(tags={**current, "resolved": value})
        )
        assert "resolved" not in await _tags(service, member)
    await service.projects.set_resolved(member, True)
    await service.projects.update_conversation_from_client(
        member, UpdateConversationRequest(tags=current)
    )
    assert (await _tags(service, member))["resolved"] == "user"


@pytest.mark.asyncio
async def test_a_client_patch_cannot_join_a_project(client, service, tmp_path):
    project = (await client.post("/api/projects", json={"name": "P"})).json()["id"]
    conversation = await _start(service, tmp_path, tags={"env": "dev"})

    with pytest.raises(Unprocessable):
        await service.projects.update_conversation_from_client(
            conversation,
            UpdateConversationRequest(tags={"project": project, "turn": "asking"}),
        )
    assert await _tags(service, conversation) == {"env": "dev"}


@pytest.mark.asyncio
async def test_project_keys_naming_no_project_are_the_clients_own(
    client, service, tmp_path
):
    """A project tag from before Projects, or of a deleted project, is an
    ordinary tag: no board, and the client sets and clears all five keys."""
    tags = {
        "project": uuid4().hex,
        "turn": "asking",
        "ask": "Mine",
        "resolved": "user",
        "resolvenote": "note",
    }
    conversation = await _start(service, tmp_path, tags=tags)

    await service.projects.update_conversation_from_client(
        conversation, UpdateConversationRequest(tags={"env": "x"})
    )
    assert await _tags(service, conversation) == {"env": "x"}
    await service.projects.update_conversation_from_client(
        conversation, UpdateConversationRequest(tags=tags)
    )
    assert await _tags(service, conversation) == tags
    assert conversation not in service.projects._watched


@pytest.mark.projects_off
@pytest.mark.asyncio
async def test_with_projects_off_a_patch_sets_and_clears_the_project_keys(
    service, stores, tmp_path
):
    project = ProjectItem(name="P")
    stores.projects.save(PersistedProjects(projects=[project]))
    tags = {
        "project": project.id.hex,
        "turn": "asking",
        "ask": "Mine",
        "resolved": "user",
        "resolvenote": "note",
    }
    conversation = await _start(service, tmp_path)

    await service.projects.update_conversation_from_client(
        conversation, UpdateConversationRequest(tags=tags)
    )
    assert await _tags(service, conversation) == tags
    await service.projects.update_conversation_from_client(
        conversation, UpdateConversationRequest(tags={})
    )
    assert await _tags(service, conversation) == {}


@pytest.mark.asyncio
async def test_the_recipient_sees_the_senders_id(client, service, tmp_path):
    """Engel: a client may show the sender's mention as its title alone, but
    the stored message, which is what the recipient's model reads, carries the
    sender's id."""
    sender = await _start(service, tmp_path, title="Reply with OK")
    worker = await _start(service, tmp_path)

    response = await client.post(
        f"/api/conversations/{worker.hex}/events",
        json=_send("FYI the palette file is ready.", sender.hex, run=False),
    )

    assert response.status_code == 200
    event_service = await service.get_event_service(worker)
    assert event_service is not None
    state = await event_service.get_state()
    [message] = [
        e for e in state.events if isinstance(e, MessageEvent) and e.source == "user"
    ]
    header = message.llm_message.content[0]
    assert isinstance(header, TextContent)
    print(header.text)
    assert header.text.startswith(
        f"[Sent by [Reply with OK](conversation:{sender.hex}), "
    )


def test_a_workspace_file_opens_where_files_cannot_be_nonblocking(
    tmp_path, monkeypatch
):
    """Windows has no O_NONBLOCK, and set_blocking fails on a regular file."""
    import os

    from openhands.agent_server.project_artifacts import open_workspace_file

    (tmp_path / "notes.md").write_bytes(b"hello")
    monkeypatch.delattr(os, "O_NONBLOCK", raising=False)

    def refuse(fd, blocking):
        raise OSError("not a pipe")

    monkeypatch.setattr(os, "set_blocking", refuse)

    with open_workspace_file(tmp_path, "notes.md") as file:
        assert file.read() == b"hello"
