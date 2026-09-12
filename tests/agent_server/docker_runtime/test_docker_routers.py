"""End-to-end tests for the docker-runtime FastAPI routers.

The per-conversation "inner" container is replaced by a real FastAPI app
bound to an ephemeral localhost port, plumbed in via a stub registry that
mimics :class:`DockerConversationRegistry`. The outer agent-server runs
under :class:`TestClient`, so any shape-of-the-wire bug in the proxy
layer would show up here.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import APIRouter, FastAPI, Header, Request, WebSocket
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.responses import JSONResponse

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.provisioning import RuntimeProvisioningStore
from openhands.agent_server.models import (
    ConversationRuntimeInfo,
    ConversationRuntimeStatus,
)


# ---------------------------------------------------------------------------
# Fake inner agent-server (FastAPI) bound to a real port
# ---------------------------------------------------------------------------


def _build_inner_app(session_key: str) -> FastAPI:
    """A minimal FastAPI app shaped like the per-conversation agent-server."""
    app = FastAPI()

    def _check(authorization: str | None) -> bool:
        return authorization == session_key

    @app.get("/server_info")
    async def server_info():
        return {"capabilities": []}

    api = APIRouter(prefix="/api")

    @api.post("/conversations")
    async def create_conversation(
        payload: dict,
        x_session_api_key: str = Header(default=""),
    ):
        if not _check(x_session_api_key):
            return {"detail": "unauthorized"}, 401
        # Magic flag used by the retry-doesn't-stop-existing-container
        # test to drive the inner-server-rejects-the-create branch
        # deterministically.
        if payload.get("tags", {}).get("forceerror"):
            from fastapi import HTTPException

            raise HTTPException(status_code=400, detail="forced")
        return {"id": payload.get("conversation_id"), "echoed": payload}

    @api.delete("/conversations/{cid}")
    async def delete_conversation(
        cid: str, x_session_api_key: str = Header(default="")
    ):
        if not _check(x_session_api_key):
            return {"detail": "unauthorized"}, 401
        return {"deleted": cid}

    @api.get("/conversations/{cid}/boundary-inspect")
    async def inspect_boundary(cid: str, request: Request):
        return JSONResponse(
            {"headers": dict(request.headers), "query": dict(request.query_params)},
            headers={"set-cookie": "oh_workspace_session_key=inner"},
        )

    @api.get("/conversations/{cid}/events/search")
    async def search_events(cid: str):
        return {"items": [{"id": "inner-event", "conversation_id": cid}]}

    @api.get("/conversations/{cid}/run")
    async def get_run(cid: str, x_session_api_key: str = Header(default="")):
        if not _check(x_session_api_key):
            return {"detail": "unauthorized"}, 401
        return {"cid": cid, "status": "running"}

    @api.get("/conversations/{cid}/workspace/{file_path:path}")
    async def serve_workspace(
        cid: str,
        file_path: str,
        x_session_api_key: str = Header(default=""),
    ):
        if not _check(x_session_api_key):
            return {"detail": "unauthorized"}, 401
        return {"file": file_path, "cid": cid}

    # The bash router is one of the global routers that's reverse-proxied
    # via ``?cid=``; mimic it so the proxy test has a real upstream to hit.
    @api.get("/bash/sessions")
    async def list_bash_sessions(x_session_api_key: str = Header(default="")):
        if not _check(x_session_api_key):
            return {"detail": "unauthorized"}, 401
        return {"sessions": []}

    app.include_router(api)

    @app.websocket("/sockets/events/{cid}")
    async def events_ws(websocket: WebSocket, cid: str):
        if websocket.headers.get("x-session-api-key") != session_key:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        await websocket.send_text(f"hello {cid}")
        try:
            while True:
                msg = await websocket.receive_text()
                await websocket.send_text(f"echo:{msg}")
        except Exception:
            pass

    return app


@contextmanager
def _run_inner_app(session_key: str):
    """Run the fake inner app on a real localhost port."""
    app = _build_inner_app(session_key)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    port: int | None = None
    while time.time() < deadline:
        if server.started and server.servers:
            sockets = list(server.servers)[0].sockets
            if sockets:
                port = sockets[0].getsockname()[1]
                break
        time.sleep(0.05)
    if port is None:
        raise RuntimeError("inner app failed to bind")
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Stub registry wired into the outer app
# ---------------------------------------------------------------------------


@dataclass
class _FakeWorkspace:
    """Stub ``DockerWorkspace``-shaped object that only carries the two
    attributes the proxy routers touch."""

    host: str
    api_key: str | None
    scoped_runtime_verified: bool = False


@dataclass
class _StubRegistry:
    """A stand-in :class:`DockerConversationRegistry` that points every
    conversation at a pre-existing real HTTP server (the fake inner app)."""

    port: int
    session_key: str
    conversations_dir: Path
    _workspaces: dict[UUID, _FakeWorkspace] = field(default_factory=dict)
    _locks: dict[UUID, asyncio.Lock] = field(default_factory=dict)

    def mutation_lock(self, cid):
        return self._locks.setdefault(cid, asyncio.Lock())

    @property
    def config(self):
        return Config(
            conversations_path=self.conversations_dir,
            workspace_path=self.conversations_dir.parent / "project",
            secret_key=SecretStr("outer-test-encryption"),
        )

    @property
    def provisioning(self):
        return RuntimeProvisioningStore(self.config)

    async def prepare(self, cid):
        self.provisioning.create(cid)

    def _make(self) -> _FakeWorkspace:
        return _FakeWorkspace(
            host=f"http://127.0.0.1:{self.port}",
            api_key=self.session_key,
        )

    def preregister(self, cid: UUID) -> _FakeWorkspace:
        """Test helper: seed the registry with a pre-existing container so
        the next ``get_or_create(cid)`` hits the ``is_new=False`` path."""
        self._workspaces[cid] = self._make()
        return self._workspaces[cid]

    def get(self, cid: UUID) -> _FakeWorkspace | None:
        return self._workspaces.get(cid)

    def runtime_info(self, cid: UUID) -> ConversationRuntimeInfo:
        directory = self.conversation_dir(cid)
        can_resume = (
            (directory / "meta.json").is_file()
            and (directory / "base_state.json").is_file()
            and self.provisioning.manifest_path(cid).is_file()
        )
        return ConversationRuntimeInfo(
            runtime_status=(
                ConversationRuntimeStatus.AVAILABLE
                if cid in self._workspaces
                else ConversationRuntimeStatus.MISSING
            ),
            can_resume=can_resume,
        )

    def conversation_dir(self, cid: UUID) -> Path:
        return self.conversations_dir / cid.hex

    def workspace_dir(self, cid: UUID) -> Path:
        return self.conversations_dir.parent / "project" / cid.hex

    async def get_or_create(self, cid: UUID) -> tuple[_FakeWorkspace, bool]:
        if cid not in self._workspaces:
            self._workspaces[cid] = self._make()
            return self._workspaces[cid], True
        return self._workspaces[cid], False

    async def stop(self, cid: UUID) -> bool:
        return self._workspaces.pop(cid, None) is not None

    async def shutdown(self) -> None:
        self._workspaces.clear()


@pytest.fixture
def docker_app(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    """Spin up the docker-mode outer FastAPI app + a fake inner server.

    We deliberately do NOT enter the lifespan context: the lifespan starts
    a tmux/vscode/desktop service we don't want to drag into these tests.
    Instead we set ``docker_registry`` directly on ``app.state``, which is
    what the lifespan would do in docker mode.
    """
    session_key = "inner-secret"
    with _run_inner_app(session_key) as port:
        app = create_app(
            Config(
                conversation_runtime="docker",
                session_api_keys=[],
                conversations_path=tmp_path / "conversations",
            )
        )
        app.state.docker_registry = _StubRegistry(
            port=port,
            session_key=session_key,
            conversations_dir=tmp_path / "conversations",
        )
        client = TestClient(app)
        try:
            yield client, app
        finally:
            client.close()


# ---------------------------------------------------------------------------
# /api/conversations — POST and per-cid catch-all
# ---------------------------------------------------------------------------


def test_post_conversations_spawns_and_forwards(docker_app):
    client, app = docker_app
    body = {
        "workspace": {"working_dir": "/workspace"},
        "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
    }
    resp = client.post("/api/conversations", json=body)
    assert resp.status_code == 200
    payload = resp.json()

    inner_payload = payload["echoed"]
    cid = UUID(inner_payload["conversation_id"])
    assert inner_payload["workspace"] == {
        "kind": "LocalWorkspace",
        "working_dir": "/workspace",
    }
    assert app.state.docker_registry.get(cid) is not None


def test_subpath_proxied_to_inner_server(docker_app):
    client, _ = docker_app
    create = client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": "/workspace"},
            "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
        },
    )
    cid = UUID(create.json()["echoed"]["conversation_id"])

    run = client.get(f"/api/conversations/{cid}/run")
    assert run.status_code == 200
    assert run.json() == {"cid": str(cid), "status": "running"}

    workspace = client.get(f"/api/conversations/{cid}/workspace/foo/bar.txt")
    assert workspace.status_code == 200
    assert workspace.json() == {"file": "foo/bar.txt", "cid": str(cid)}


def test_subpath_returns_404_when_no_container(docker_app):
    client, _ = docker_app
    cid = uuid4()
    resp = client.get(f"/api/conversations/{cid}/run")
    assert resp.status_code == 404


def test_delete_proxies_then_stops_container_and_removes_host_state(docker_app):
    client, app = docker_app
    create = client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": "/workspace"},
            "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
        },
    )
    cid = UUID(create.json()["echoed"]["conversation_id"])
    assert app.state.docker_registry.get(cid) is not None
    conversation_dir = app.state.docker_registry.conversation_dir(cid)
    conversation_dir.mkdir(parents=True)
    (conversation_dir / "meta.json").write_text("{}")
    conversation_dir.chmod(0o200)
    workspace_dir = app.state.docker_registry.workspace_dir(cid)
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "marker.txt").write_text("nested")

    delete = client.delete(f"/api/conversations/{cid}")
    assert delete.status_code == 200
    assert delete.json() == {"deleted": str(cid)}
    assert app.state.docker_registry.get(cid) is None
    assert not conversation_dir.exists()
    assert not workspace_dir.exists()


# ---------------------------------------------------------------------------
# Global routers (bash/git/file/...) require ?cid=…
# ---------------------------------------------------------------------------


def test_global_router_requires_cid_in_docker_mode(docker_app):
    """A request to ``/api/bash/...`` without ``?cid=`` must surface a
    clear 400 rather than silently falling through to a local handler."""
    client, _ = docker_app
    resp = client.get("/api/bash/sessions")
    assert resp.status_code == 400
    assert "cid" in resp.json()["detail"]


def test_global_router_forwarded_with_cid(docker_app):
    """With ``?cid=…`` the global router proxies to the matching
    sub-container."""
    client, app = docker_app
    cid = uuid4()
    app.state.docker_registry.preregister(cid)

    resp = client.get(f"/api/bash/sessions?cid={cid}")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": []}


def test_global_router_404_for_unknown_cid(docker_app):
    client, _ = docker_app
    resp = client.get(f"/api/bash/sessions?cid={uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Read-only metadata: list / count / search / get are served by the LOCAL
# ``conversation_router`` reading the shared persistence dir, NOT by a
# proxy. We don't exhaustively test the metadata semantics (they're
# covered by ``test_conversation_service.py``), only that the routes are
# wired and behave at the wire level.
# ---------------------------------------------------------------------------


def test_runtime_inspection_does_not_start_container(docker_app):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")

    response = client.get(f"/api/conversations/{cid}/runtime")

    assert response.status_code == 200
    assert response.json() == {
        "runtime_status": "missing",
        "can_resume": True,
        "runtime_error": None,
    }
    assert registry.get(cid) is None


def test_runtime_release_preserves_history_and_can_reprovision(docker_app):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")
    registry.preregister(cid)

    assert client.delete(f"/api/conversations/{cid}/runtime").status_code == 204
    assert client.delete(f"/api/conversations/{cid}/runtime").status_code == 204
    assert (directory / "base_state.json").is_file()
    info = client.get(f"/api/conversations/{cid}/runtime").json()
    assert info["runtime_status"] == "missing"
    assert info["can_resume"] is True
    restored = client.post(f"/api/conversations/{cid}/runtime/reprovision")
    assert restored.json()["runtime_status"] == "available"


def test_runtime_release_rejects_unknown_conversation(docker_app):
    client, _ = docker_app
    assert client.delete(f"/api/conversations/{uuid4()}/runtime").status_code == 404


def test_runtime_reprovision_starts_infrastructure_without_run(docker_app):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")

    response = client.post(f"/api/conversations/{cid}/runtime/reprovision")

    assert response.status_code == 200
    assert response.json()["runtime_status"] == "available"
    assert registry.get(cid) is not None


def test_runtime_reprovision_rejects_ownership_loss(docker_app):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")

    response = client.post(f"/api/conversations/{cid}/runtime/reprovision")

    assert response.status_code == 409
    assert registry.get(cid) is None


def test_metadata_routes_are_mounted_locally_in_docker_mode(tmp_path):
    """``GET /api/conversations``, ``/api/conversations/count``, and
    ``/api/conversations/search`` must come from the LOCAL conversation
    router in docker mode — they read the shared persistence dir on
    disk, the docker proxy does not intercept them.

    We assert by inspecting the registered routes (not by hitting the
    endpoints) because the lifespan that initializes the conversation
    service isn't entered in these unit tests.
    """
    app = create_app(
        Config(
            conversation_runtime="docker",
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
        )
    )
    paths = {getattr(r, "path", None): getattr(r, "endpoint", None) for r in app.routes}
    # Existence: the local conversation_router exposes these.
    assert "/api/conversations" in paths
    assert "/api/conversations/count" in paths
    assert "/api/conversations/search" in paths
    # Their endpoints must come from ``conversation_router``, not from any
    # ``docker_runtime`` module.
    for p in (
        "/api/conversations",
        "/api/conversations/count",
        "/api/conversations/search",
    ):
        ep = paths[p]
        if ep is not None:
            assert "docker_runtime" not in ep.__module__


def test_local_mode_routes_are_unchanged(tmp_path):
    """Sanity check: enabling docker mode must not have leaked into local."""
    app = create_app(
        Config(
            conversation_runtime="local",
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
        )
    )
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/conversations" in paths
    # The docker-mode catch-all path must NOT appear in local mode.
    assert "/api/conversations/{conversation_id}/{tail:path}" not in paths


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------


def test_websocket_bridges_to_inner_server(docker_app):
    client, app = docker_app
    cid = uuid4()
    app.state.docker_registry.preregister(cid)

    with client.websocket_connect(f"/sockets/events/{cid}") as ws:
        greeting = ws.receive_text()
        assert greeting == f"hello {cid}"
        ws.send_text("ping")
        assert ws.receive_text() == "echo:ping"


def test_websocket_closes_when_conversation_unknown(docker_app):
    """When no container exists for the requested conversation, the bridge
    must close the (already-accepted) socket with 1008."""
    from starlette.websockets import WebSocketDisconnect

    client, _ = docker_app
    cid = uuid4()
    with client.websocket_connect(f"/sockets/events/{cid}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1008


# ---------------------------------------------------------------------------
# WebSocket auth — regression guards for the original review findings
# ---------------------------------------------------------------------------


@pytest.fixture
def docker_app_with_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "global"))
    """Docker-mode app with ``session_api_keys`` configured."""
    session_key = "inner-secret"
    outer_key = "outer-secret"
    with _run_inner_app(session_key) as port:
        app = create_app(
            Config(
                conversation_runtime="docker",
                session_api_keys=[outer_key],
                conversations_path=tmp_path / "conversations",
            )
        )
        app.state.docker_registry = _StubRegistry(
            port=port,
            session_key=session_key,
            conversations_dir=tmp_path / "conversations",
        )
        client = TestClient(app)
        try:
            yield client, app, outer_key
        finally:
            client.close()


def test_websocket_rejects_wrong_session_key(docker_app_with_auth):
    """A WS upgrade carrying a wrong key in the query string must be
    rejected BEFORE the connection is accepted."""
    client, app, _outer_key = docker_app_with_auth
    cid = uuid4()
    app.state.docker_registry.preregister(cid)

    with pytest.raises(Exception):
        with client.websocket_connect(
            f"/sockets/events/{cid}?session_api_key=wrong",
        ):
            pass


def test_websocket_rejects_missing_first_message_auth(docker_app_with_auth):
    """No key supplied at upgrade -> the helper accepts the socket for
    first-message-auth and closes 4001 on a non-auth frame."""
    from starlette.websockets import WebSocketDisconnect

    client, app, _outer_key = docker_app_with_auth
    cid = uuid4()
    app.state.docker_registry.preregister(cid)

    with client.websocket_connect(f"/sockets/events/{cid}") as ws:
        ws.send_text("not an auth frame")
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 4001


def test_websocket_accepts_with_valid_outer_key(docker_app_with_auth):
    """The correct outer session key must bridge to the inner server."""
    client, app, outer_key = docker_app_with_auth
    cid = uuid4()
    app.state.docker_registry.preregister(cid)

    with client.websocket_connect(
        f"/sockets/events/{cid}?session_api_key={outer_key}",
    ) as ws:
        assert ws.receive_text() == f"hello {cid}"
        ws.send_text("ping")
        assert ws.receive_text() == "echo:ping"


# ---------------------------------------------------------------------------
# Idempotent POST retry semantics
# ---------------------------------------------------------------------------


def test_post_retry_does_not_stop_existing_container_on_inner_4xx(docker_app):
    """If ``get_or_create()`` returns an existing workspace (``is_new=False``)
    and the inner server then returns a 4xx, we MUST leave the container
    running."""
    client, app = docker_app

    cid = uuid4()
    app.state.docker_registry.preregister(cid)
    assert app.state.docker_registry.get(cid) is not None

    resp = client.post(
        "/api/conversations",
        json={
            "conversation_id": str(cid),
            "workspace": {},
            "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
            "tags": {"forceerror": "true"},
        },
    )
    assert resp.status_code == 400
    # The live container survived the failed retry.
    assert app.state.docker_registry.get(cid) is not None


def test_post_first_create_tears_down_on_inner_4xx(docker_app):
    """When ``get_or_create()`` spawned a fresh workspace (``is_new=True``)
    and the inner server rejects the create, the workspace IS torn down
    so we don't leak."""
    client, app = docker_app

    cid = uuid4()
    assert app.state.docker_registry.get(cid) is None

    resp = client.post(
        "/api/conversations",
        json={
            "conversation_id": str(cid),
            "workspace": {},
            "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
            "tags": {"forceerror": "true"},
        },
    )
    assert resp.status_code == 400
    assert app.state.docker_registry.get(cid) is None


# ---------------------------------------------------------------------------
# Workspace static-file proxy is registered under the cookie-auth group
# ---------------------------------------------------------------------------


def test_workspace_router_registered_under_cookie_auth_in_docker_mode(tmp_path):
    """In docker mode the workspace path must be registered before the
    catch-all so it isn't shadowed by header-only auth."""
    app = create_app(
        Config(
            conversation_runtime="docker",
            session_api_keys=[],
            conversations_path=tmp_path / "conversations",
        )
    )

    workspace_path = "/api/conversations/{conversation_id}/workspace/{file_path:path}"
    catchall_path = "/api/conversations/{conversation_id}/{tail:path}"

    workspace_route_index = next(
        i
        for i, route in enumerate(app.routes)
        if getattr(route, "path", None) == workspace_path
    )
    catchall_route_index = next(
        i
        for i, route in enumerate(app.routes)
        if getattr(route, "path", None) == catchall_path
    )
    assert workspace_route_index < catchall_route_index


@pytest.mark.parametrize(
    "path",
    [
        "/api/llm/models/verified",
        "/api/llm/providers",
        "/api/llm/provider-connections",
        "/api/llm/subscription/openai/models",
    ],
)
def test_llm_discovery_without_conversation(docker_app, path):
    client, app = docker_app
    response = client.get(path)
    assert response.status_code == 200
    assert isinstance(response.json(), (dict, list))
    assert not app.state.docker_registry._workspaces


def test_workspace_discovery_without_conversation(docker_app, tmp_path):
    client, app = docker_app
    response = client.get("/api/file/home")
    assert response.status_code == 200
    assert response.json()["home"] == str(Path.home())
    response = client.get("/api/file/search_subdirs", params={"path": str(tmp_path)})
    assert response.status_code == 200
    assert not app.state.docker_registry._workspaces


def test_customization_without_conversation(docker_app):
    client, app = docker_app
    response = client.post(
        "/api/skills",
        json={
            "load_public": False,
            "load_user": False,
            "load_project": False,
            "load_org": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["skills"] == []
    assert client.get("/api/canvas-extensions/installed").status_code == 200
    assert client.post("/api/hooks", json={}).status_code == 200
    assert not app.state.docker_registry._workspaces


def test_event_search_is_served_by_container(docker_app):
    client, app = docker_app
    cid = uuid4()
    app.state.docker_registry.preregister(cid)
    response = client.get(f"/api/conversations/{cid}/events/search")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "inner-event"


@pytest.mark.parametrize(
    "path",
    [
        "/api/conversations/{cid}/events/search",
        "/api/bash/sessions?cid={cid}",
        "/api/conversations/{cid}/workspace/index.html",
    ],
)
def test_persisted_conversation_recovers_after_registry_restart(docker_app, path):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")
    assert registry.get(cid) is None
    response = client.get(path.format(cid=cid))
    assert response.status_code == 200
    assert registry.get(cid) is not None


def test_persisted_conversation_websocket_recovers_after_restart(docker_app):
    client, app = docker_app
    registry = app.state.docker_registry
    cid = uuid4()
    registry.provisioning.create(cid)
    directory = registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")
    with client.websocket_connect(f"/sockets/events/{cid}") as ws:
        assert ws.receive_text() == f"hello {cid}"


def test_unknown_conversation_does_not_start_container(docker_app):
    client, app = docker_app
    cid = uuid4()
    assert client.get(f"/api/conversations/{cid}/events/search").status_code == 404
    assert app.state.docker_registry.get(cid) is None


def test_mcp_probe_without_conversation(docker_app):
    client, app = docker_app
    script = (
        "from fastmcp import FastMCP\n"
        "mcp = FastMCP('docker-setup-test')\n"
        "@mcp.tool()\n"
        "def echo(message: str) -> str:\n"
        "    return message\n"
        "mcp.run()\n"
    )
    response = client.post(
        "/api/mcp/test",
        json={
            "server": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-c", script],
            },
            "timeout": 15,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["tools"] == ["echo"]
    assert not app.state.docker_registry._workspaces


@pytest.mark.parametrize(
    "method,path,payload,expected_status",
    [
        ("post", "/api/mcp/test", {}, 422),
        ("post", "/api/mcp/oauth/start", {}, 422),
        ("get", "/api/mcp/oauth/status/unknown", None, 404),
        (
            "post",
            "/api/mcp/oauth/callback/unknown",
            {"callback_url": "http://127.0.0.1/callback?code=test"},
            404,
        ),
    ],
)
def test_mcp_setup_routes_without_conversation(
    docker_app, method, path, payload, expected_status
):
    client, app = docker_app
    response = client.request(method, path, json=payload)
    assert response.status_code == expected_status, response.text
    assert not app.state.docker_registry._workspaces


def test_tool_catalog_is_available_before_conversation(docker_app):
    client, app = docker_app
    response = client.get("/api/tools/")
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)
    assert response.json()
    assert not app.state.docker_registry._workspaces


def test_selected_project_is_not_silently_replaced(docker_app, tmp_path):
    client, app = docker_app
    project = tmp_path / "selected-project"
    project.mkdir()
    (project / "project.txt").write_text("selected project contents")
    response = client.post(
        "/api/conversations",
        json={
            "workspace": {"working_dir": str(project)},
            "agent": {"kind": "Agent", "llm": {"model": "test-model"}},
        },
    )
    # Unsupported host workspaces must be rejected, not accepted as empty ones.
    if 400 <= response.status_code < 500:
        assert not app.state.docker_registry._workspaces
        return
    assert response.status_code == 200, response.text
    cid = UUID(response.json()["echoed"]["conversation_id"])
    selected_file = app.state.docker_registry.workspace_dir(cid) / "project.txt"
    assert selected_file.is_file(), "Creation silently discarded the selected project"
    assert selected_file.read_text() == "selected project contents"


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_BOUNDARY_TESTS") != "1",
    reason="Requires an existing local agent-server Docker image",
)
def test_host_mcp_probe_does_not_claim_container_verification(docker_app, tmp_path):
    client, _ = docker_app
    executable = tmp_path / "host-only-mcp"
    executable.write_text(
        f"#!{sys.executable}\n"
        "from fastmcp import FastMCP\n"
        "m = FastMCP('host-only')\n"
        "@m.tool()\n"
        "def echo(message: str) -> str: return message\n"
        "m.run()\n"
    )
    executable.chmod(0o700)
    response = client.post(
        "/api/mcp/test",
        json={"server": {"transport": "stdio", "command": str(executable)}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["scope"] == "host"
    assert response.json()["runtime_verified"] is False
    # Only the Docker boundary is external: the probe and executable are real.
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--entrypoint",
            "/bin/sh",
            "ghcr.io/openhands/agent-server:latest-python",
            "-c",
            'test -x "$1"',
            "sh",
            str(executable),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1, result.stderr


def test_scoped_runtime_requires_capable_inner_image(docker_app):
    client, app = docker_app
    cid = uuid4()
    app.state.docker_registry.preregister(cid)
    response = client.get(f"/api/conversations/{cid}/vscode/status")
    assert response.status_code == 409
    assert "image" in response.json()["detail"]
    legacy = client.get(f"/api/bash/sessions?cid={cid}")
    assert legacy.status_code == 200


def test_scoped_runtime_auth_and_unknown_conversation(docker_app):
    client, app = docker_app
    response = client.get(f"/api/conversations/{uuid4()}/vscode/status")
    assert response.status_code == 404
    app.state.config = app.state.config.model_copy(
        update={"session_api_keys": ["secret"]}
    )
    response = client.get(f"/api/conversations/{uuid4()}/vscode/status")
    assert response.status_code == 401


def test_inner_key_cannot_authenticate_outer_api(docker_app_with_auth):
    client, _, _ = docker_app_with_auth
    response = client.get(
        "/api/settings/secrets", headers={"X-Session-API-Key": "inner-secret"}
    )
    assert response.status_code == 401


def test_legacy_recovery_fails_without_deleting_state(docker_app):
    client, app = docker_app
    cid = uuid4()
    directory = app.state.docker_registry.conversation_dir(cid)
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text("{}")
    (directory / "base_state.json").write_text("{}")
    response = client.get(f"/api/conversations/{cid}/events/search")
    assert response.status_code == 409
    assert (directory / "base_state.json").read_text() == "{}"


def test_runtime_fork_is_explicitly_unsupported(docker_app):
    client, app = docker_app
    cid = uuid4()
    app.state.docker_registry.preregister(cid)
    response = client.post(f"/api/conversations/{cid}/fork", json={})
    assert response.status_code == 501


def test_proxy_strips_outer_credentials_and_runtime_cookies(docker_app_with_auth):
    client, app, outer_key = docker_app_with_auth
    cid = uuid4()
    app.state.docker_registry.preregister(cid)
    response = client.get(
        f"/api/conversations/{cid}/boundary-inspect?session_api_key={outer_key}&keep=value",
        headers={
            "X-Session-API-Key": outer_key,
            "Authorization": "Bearer " + outer_key,
            "Cookie": "oh_workspace_session_key=" + outer_key,
        },
    )
    assert response.status_code == 200
    forwarded = response.json()
    assert forwarded["headers"]["x-session-api-key"] == "inner-secret"
    assert "authorization" not in forwarded["headers"]
    assert "cookie" not in forwarded["headers"]
    assert forwarded["query"] == {"keep": "value"}
    assert "set-cookie" not in response.headers
