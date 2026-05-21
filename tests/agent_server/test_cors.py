"""Tests for the agent-server CORS dispatcher.

Covers:

1. The historical localhost-always-allowed behavior (OpenHands/OpenHands#4624)
   — including the regression in OpenHands/OpenHands#8675 where setting an
   explicit allowlist silently disabled the localhost bypass.
2. The wildcard CORS on the workspace-session and workspace static-file
   routes (the only paths using cookie-based ambient auth).
3. Dispatch on the post-``root_path`` request path, so reverse-proxy
   deployments that mount this server under a sub-path still match the
   workspace routes correctly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import (
    WORKSPACE_SESSION_COOKIE_NAME,
    get_conversation_service,
)
from openhands.agent_server.event_service import EventService
from openhands.agent_server.middleware import (
    CORSDispatcher,
    LocalhostCORSMiddleware,
    _is_workspace_cookie_path,
)
from openhands.sdk.workspace import LocalWorkspace


SESSION_KEY = "test-key-cors"
GLOBAL_ORIGIN = "https://gui.example.com"
LOCALHOST_ORIGIN = "http://localhost:3000"
LOOPBACK_ORIGIN = "http://127.0.0.1:5173"
DOCKER_HOST_IP = "192.168.1.206"
DOCKER_HOST_ORIGIN = f"http://{DOCKER_HOST_IP}:42015"
REMOTE_ORIGIN = "https://canvas.example.com"
OTHER_ORIGIN = "https://attacker.example.com"


def _build_client(tmp_path, *, conversation_id: UUID, config: Config) -> TestClient:
    event_service = AsyncMock(spec=EventService)
    event_service.stored = SimpleNamespace(
        workspace=LocalWorkspace(working_dir=str(tmp_path))
    )
    conversation_service = AsyncMock(spec=ConversationService)

    async def _get_event_service(cid: UUID):
        if cid == conversation_id:
            return event_service
        return None

    conversation_service.get_event_service.side_effect = _get_event_service

    app = create_app(config)
    app.dependency_overrides[get_conversation_service] = lambda: conversation_service
    return TestClient(app, raise_server_exceptions=False)


def _preflight(
    client: TestClient,
    path: str,
    *,
    origin: str,
    method: str = "POST",
    request_headers: str = "x-session-api-key,content-type",
):
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": request_headers,
        },
    )


# ---- workspace-cookie routes: wildcard CORS, with credentials --------------


def test_workspace_session_accepts_any_origin(tmp_path):
    """The workspace-session endpoint accepts CORS from any origin,
    regardless of ``allow_cors_origins``. The cookie's own
    ``X-Session-API-Key`` mint requirement and ``Partitioned`` (CHIPS)
    scope are the actual security boundary; CORS is not."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    for origin in (REMOTE_ORIGIN, OTHER_ORIGIN, "https://random.example"):
        resp = _preflight(client, "/api/auth/workspace-session", origin=origin)
        assert resp.status_code == 200, origin
        # allow_credentials=True forces Starlette to echo the Origin back
        # rather than emit a literal "*".
        assert resp.headers["access-control-allow-origin"] == origin, origin
        assert resp.headers["access-control-allow-credentials"] == "true", origin


def test_workspace_static_accepts_any_origin(tmp_path):
    cid = uuid4()
    client = _build_client(
        tmp_path,
        conversation_id=cid,
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    for origin in (REMOTE_ORIGIN, OTHER_ORIGIN):
        resp = _preflight(
            client,
            f"/api/conversations/{cid}/workspace/report.html",
            origin=origin,
            method="GET",
        )
        assert resp.status_code == 200, origin
        assert resp.headers["access-control-allow-origin"] == origin, origin
        assert resp.headers["access-control-allow-credentials"] == "true", origin


# ---- workspace-cookie routes: actual responses (not just preflight) --------
# These tests cover the QA-found bug where ``allow_origins=["*"]`` made
# Starlette emit ``Access-Control-Allow-Origin: *`` on the actual POST/DELETE
# response unless the request already carried a Cookie header — which
# real browsers reject for credentialed CORS. The fix is to use
# ``allow_origin_regex=r".*"`` so the request Origin is echoed on every
# response, not just preflight.


def test_workspace_session_post_response_echoes_origin_no_cookie(tmp_path):
    """The very first mint request from a browser does NOT carry a
    Cookie (it's the one creating the cookie). The response must still
    echo the Origin — not emit ``*`` — or the browser rejects the
    credentialed response and the cookie never gets set."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = client.post(
        "/api/auth/workspace-session",
        headers={
            "X-Session-API-Key": SESSION_KEY,
            "Origin": REMOTE_ORIGIN,
        },
    )
    assert resp.status_code == 204
    # The actual response must echo the origin — not "*" — for the
    # browser to accept it with credentials.
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    # Vary: Origin is required so caches don't collapse responses
    # across origins.
    assert "Origin" in resp.headers.get("vary", "")
    # And the cookie itself is actually set.
    assert WORKSPACE_SESSION_COOKIE_NAME in resp.cookies


def test_workspace_session_delete_response_echoes_origin(tmp_path):
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = client.delete(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY, "Origin": REMOTE_ORIGIN},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_workspace_static_get_response_echoes_origin(tmp_path):
    """Actual GET against a workspace static file from an arbitrary
    origin must also echo the Origin (not ``*``) for credentialed
    ``fetch()`` from JS to work."""
    (tmp_path / "report.html").write_text("<title>ok</title>")
    cid = uuid4()
    client = _build_client(
        tmp_path,
        conversation_id=cid,
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = client.get(
        f"/api/conversations/{cid}/workspace/report.html",
        headers={
            "X-Session-API-Key": SESSION_KEY,
            "Origin": REMOTE_ORIGIN,
        },
    )
    assert resp.status_code == 200
    assert resp.text == "<title>ok</title>"
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


# ---- non-workspace routes: standard CORS, configurable ---------------------


def test_non_workspace_routes_honor_allow_cors_origins(tmp_path):
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY], allow_cors_origins=[GLOBAL_ORIGIN]
        ),
    )

    resp = _preflight(client, "/api/conversations", origin=GLOBAL_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == GLOBAL_ORIGIN


def test_non_workspace_routes_reject_unlisted_origin(tmp_path):
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY], allow_cors_origins=[GLOBAL_ORIGIN]
        ),
    )

    resp = _preflight(client, "/api/conversations", origin=OTHER_ORIGIN)
    assert "access-control-allow-origin" not in resp.headers


def test_workspace_wildcard_does_not_bleed_into_other_api(tmp_path):
    """Sanity check that the wildcard on workspace routes is scoped —
    non-workspace endpoints still respect ``allow_cors_origins`` (empty
    by default)."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = _preflight(client, "/api/conversations", origin=OTHER_ORIGIN)
    assert "access-control-allow-origin" not in resp.headers


# ---- localhost / DOCKER_HOST_ADDR auto-allow (regression coverage) ---------


@pytest.mark.parametrize("origin", [LOCALHOST_ORIGIN, LOOPBACK_ORIGIN])
def test_localhost_allowed_with_empty_allow_origins(tmp_path, origin):
    """Original PR #4624 behavior: any localhost/127.0.0.1 port works."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = _preflight(client, "/api/conversations", origin=origin)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", [LOCALHOST_ORIGIN, LOOPBACK_ORIGIN])
def test_localhost_allowed_when_allow_origins_is_set(tmp_path, origin):
    """Regression test for OpenHands/OpenHands#8675: setting an explicit
    ``allow_cors_origins`` must NOT disable the localhost auto-allow.

    The ``Config.allow_cors_origins`` docstring promises localhost is
    always accepted regardless of what's in the list; the historical
    implementation violated that as soon as the list was non-empty."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY], allow_cors_origins=[GLOBAL_ORIGIN]
        ),
    )

    resp = _preflight(client, "/api/conversations", origin=origin)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


def test_docker_host_addr_allowed_when_allow_origins_is_set(tmp_path, monkeypatch):
    """Same regression as localhost, for the DOCKER_HOST_ADDR auto-allow
    added by software-agent-sdk#1466."""
    monkeypatch.setenv("DOCKER_HOST_ADDR", DOCKER_HOST_IP)
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY], allow_cors_origins=[GLOBAL_ORIGIN]
        ),
    )

    resp = _preflight(client, "/api/conversations", origin=DOCKER_HOST_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == DOCKER_HOST_ORIGIN


# ---- root_path handling ----------------------------------------------------


@pytest.mark.asyncio
async def test_dispatcher_matches_workspace_path_after_root_path_strip():
    """When the server is mounted under a sub-path (FastAPI ``root_path``
    / ``OH_WEB_URL``), the raw ``scope['path']`` still includes that
    prefix. Dispatch must use the route path (post-strip), or workspace
    routes are misrouted to the default CORS middleware."""

    captured: dict[str, str] = {}

    async def downstream(scope, receive, send):
        # Record which underlying middleware saw the request by tagging
        # via send().
        captured["path"] = scope["path"]
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    dispatcher = CORSDispatcher(downstream, allow_origins=[])
    # If dispatch is correct, this scope should be routed through the
    # *workspace* CORS middleware, which echoes the Origin back.
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "OPTIONS",
        "scheme": "http",
        "path": "/runtime/abc/api/auth/workspace-session",
        "root_path": "/runtime/abc",
        "query_string": b"",
        "headers": [
            (b"origin", REMOTE_ORIGIN.encode()),
            (b"access-control-request-method", b"POST"),
            (b"access-control-request-headers", b"x-session-api-key"),
        ],
    }
    await dispatcher(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
    assert headers.get("access-control-allow-origin") == REMOTE_ORIGIN
    assert headers.get("access-control-allow-credentials") == "true"


# ---- pure-unit checks on the path matcher ----------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/workspace-session",
        "/api/conversations/abc-123/workspace/",
        "/api/conversations/00000000-0000-0000-0000-000000000000/workspace/report.html",
        "/api/conversations/x/workspace/nested/dir/file.txt",
    ],
)
def test_is_workspace_cookie_path_matches(path):
    assert _is_workspace_cookie_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/workspace-sessions",  # extra char, not the real route
        "/api/conversations",
        "/api/conversations/abc/events",
        "/api/conversations/abc/workspaces/file",  # plural, wrong segment
        "/api/auth/login",
        "/",
        "",
    ],
)
def test_is_workspace_cookie_path_rejects(path):
    assert not _is_workspace_cookie_path(path)


# ---- pure-unit checks on the LocalhostCORSMiddleware override --------------


async def _noop_app(scope, receive, send):  # pragma: no cover - never called
    return None


def test_localhost_middleware_localhost_is_unconditional():
    """``is_allowed_origin`` must auto-allow localhost regardless of
    what's in the configured allow list."""
    m = LocalhostCORSMiddleware(app=_noop_app, allow_origins=[GLOBAL_ORIGIN])
    assert m.is_allowed_origin("http://localhost:9999")
    assert m.is_allowed_origin("http://127.0.0.1:5173")


def test_localhost_middleware_docker_host_addr_is_unconditional(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST_ADDR", DOCKER_HOST_IP)
    m = LocalhostCORSMiddleware(app=_noop_app, allow_origins=[GLOBAL_ORIGIN])
    assert m.is_allowed_origin(DOCKER_HOST_ORIGIN)


def test_localhost_middleware_other_origin_uses_allow_list():
    m = LocalhostCORSMiddleware(app=_noop_app, allow_origins=[GLOBAL_ORIGIN])
    assert m.is_allowed_origin(GLOBAL_ORIGIN)
    assert not m.is_allowed_origin(OTHER_ORIGIN)
