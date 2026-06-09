"""End-to-end tests for the docker-runtime FastAPI routers.

The per-conversation "inner" container is replaced by a real FastAPI app
bound to an ephemeral localhost port, plumbed in via a stub registry that
mimics :class:`DockerConversationRegistry`. The outer agent-server runs
under :class:`TestClient`, so any shape-of-the-wire bug in the proxy
layer would show up here.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
import uvicorn
from fastapi import APIRouter, FastAPI, Header, WebSocket
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config


# ---------------------------------------------------------------------------
# Fake inner agent-server (FastAPI) bound to a real port
# ---------------------------------------------------------------------------


def _build_inner_app(session_key: str) -> FastAPI:
    """A minimal FastAPI app shaped like the per-conversation agent-server."""
    app = FastAPI()

    def _check(authorization: str | None) -> bool:
        return authorization == session_key

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
        if payload.get("_force_400"):
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


@dataclass
class _StubRegistry:
    """A stand-in :class:`DockerConversationRegistry` that points every
    conversation at a pre-existing real HTTP server (the fake inner app)."""

    port: int
    session_key: str
    _workspaces: dict[UUID, _FakeWorkspace] = field(default_factory=dict)

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
def docker_app(tmp_path):
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
                conversations_path=tmp_path / "conversations",
            )
        )
        app.state.docker_registry = _StubRegistry(port=port, session_key=session_key)
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
        "workspace": {"working_dir": "/host/will-be-rewritten"},
        "agent": {"kind": "Agent"},
    }
    resp = client.post("/api/conversations", json=body)
    assert resp.status_code == 200
    payload = resp.json()

    inner_payload = payload["echoed"]
    cid = UUID(inner_payload["conversation_id"])
    assert app.state.docker_registry.get(cid) is not None


def test_subpath_proxied_to_inner_server(docker_app):
    client, _ = docker_app
    create = client.post(
        "/api/conversations",
        json={"workspace": {"working_dir": "/x"}, "agent": {}},
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


def test_delete_proxies_then_stops_container(docker_app):
    client, app = docker_app
    create = client.post(
        "/api/conversations",
        json={"workspace": {"working_dir": "/x"}, "agent": {}},
    )
    cid = UUID(create.json()["echoed"]["conversation_id"])
    assert app.state.docker_registry.get(cid) is not None

    delete = client.delete(f"/api/conversations/{cid}")
    assert delete.status_code == 200
    assert delete.json() == {"deleted": str(cid)}
    assert app.state.docker_registry.get(cid) is None


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
            conversations_path=tmp_path / "conversations",
        )
    )
    paths = {r.path for r in app.routes if hasattr(r, "path")}
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
def docker_app_with_auth(tmp_path):
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
        app.state.docker_registry = _StubRegistry(port=port, session_key=session_key)
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
            "agent": {},
            "_force_400": True,
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
            "agent": {},
            "_force_400": True,
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
