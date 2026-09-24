"""Tests for the agent-server CORS dispatcher."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config, load_config
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
        return event_service if cid == conversation_id else None

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


# Workspace cookie routes accept any http(s) origin with credentials.


def test_workspace_session_preflight_accepts_any_origin(tmp_path):
    client = _build_client(
        tmp_path, conversation_id=uuid4(), config=Config(session_api_keys=[SESSION_KEY])
    )
    for origin in (REMOTE_ORIGIN, OTHER_ORIGIN, "https://random.example"):
        resp = _preflight(client, "/api/auth/workspace-session", origin=origin)
        assert resp.status_code == 200, origin
        assert resp.headers["access-control-allow-origin"] == origin, origin
        assert resp.headers["access-control-allow-credentials"] == "true", origin


def test_workspace_static_preflight_accepts_any_origin(tmp_path):
    cid = uuid4()
    client = _build_client(
        tmp_path, conversation_id=cid, config=Config(session_api_keys=[SESSION_KEY])
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


def test_workspace_session_post_response_echoes_origin_no_cookie(tmp_path):
    """The first mint POST has no Cookie yet; the response must still
    echo Origin (not ``*``) for the browser to accept it."""
    client = _build_client(
        tmp_path, conversation_id=uuid4(), config=Config(session_api_keys=[SESSION_KEY])
    )
    resp = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY, "Origin": REMOTE_ORIGIN},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in resp.headers.get("vary", "")
    assert WORKSPACE_SESSION_COOKIE_NAME in resp.cookies


def test_workspace_session_delete_response_echoes_origin(tmp_path):
    client = _build_client(
        tmp_path, conversation_id=uuid4(), config=Config(session_api_keys=[SESSION_KEY])
    )
    resp = client.delete(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY, "Origin": REMOTE_ORIGIN},
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_workspace_static_get_response_echoes_origin(tmp_path):
    (tmp_path / "report.html").write_text("<title>ok</title>")
    cid = uuid4()
    client = _build_client(
        tmp_path, conversation_id=cid, config=Config(session_api_keys=[SESSION_KEY])
    )
    resp = client.get(
        f"/api/conversations/{cid}/workspace/report.html",
        headers={"X-Session-API-Key": SESSION_KEY, "Origin": REMOTE_ORIGIN},
    )
    assert resp.status_code == 200
    assert resp.text == "<title>ok</title>"
    assert resp.headers["access-control-allow-origin"] == REMOTE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_workspace_routes_reject_null_origin(tmp_path):
    """``Origin: null`` (sandboxed iframes, ``data:`` URLs) must not match."""
    cid = uuid4()
    client = _build_client(
        tmp_path, conversation_id=cid, config=Config(session_api_keys=[SESSION_KEY])
    )

    for path, method in [
        ("/api/auth/workspace-session", "POST"),
        (f"/api/conversations/{cid}/workspace/file", "GET"),
    ]:
        resp = _preflight(client, path, origin="null", method=method)
        assert "access-control-allow-origin" not in resp.headers

    resp = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY, "Origin": "null"},
    )
    assert "access-control-allow-origin" not in resp.headers


# Non-workspace routes honor allow_cors_origins.


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


def test_non_workspace_regex_echoes_http_origin_on_actual_response(tmp_path):
    """Regex origin matches must remain credential-compatible.

    Starlette's regex path echoes the concrete origin instead of emitting a
    literal wildcard, so browsers accept the response together with
    ``Access-Control-Allow-Credentials: true``.
    """
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY],
            allow_cors_origin_regex=r"https?://.+",
        ),
    )

    resp = client.get("/server_info", headers={"Origin": OTHER_ORIGIN})

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == OTHER_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in resp.headers.get("vary", "")


def test_json_config_regex_echoes_http_origin_on_actual_response(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"allow_cors_origin_regex": "https://.*\\\\.example\\\\.com"}'
    )
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=load_config(config_path),
    )

    resp = client.get("/server_info", headers={"Origin": "https://app.example.com"})

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://app.example.com"
    assert resp.headers["access-control-allow-credentials"] == "true"
    assert "Origin" in resp.headers.get("vary", "")


def test_non_workspace_regex_rejects_null_origin(tmp_path):
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(
            session_api_keys=[SESSION_KEY],
            allow_cors_origin_regex=r"https?://.+",
        ),
    )

    resp = _preflight(client, "/api/conversations", origin="null")

    assert "access-control-allow-origin" not in resp.headers


def test_workspace_wildcard_does_not_bleed_into_other_api(tmp_path):
    client = _build_client(
        tmp_path, conversation_id=uuid4(), config=Config(session_api_keys=[SESSION_KEY])
    )
    resp = _preflight(client, "/api/conversations", origin=OTHER_ORIGIN)
    assert "access-control-allow-origin" not in resp.headers


# Localhost / DOCKER_HOST_ADDR auto-allow regression coverage
# (OpenHands/OpenHands#4624 intent vs the #8675 regression).


@pytest.mark.parametrize("origin", [LOCALHOST_ORIGIN, LOOPBACK_ORIGIN])
def test_localhost_allowed_with_empty_allow_origins(tmp_path, origin):
    client = _build_client(
        tmp_path, conversation_id=uuid4(), config=Config(session_api_keys=[SESSION_KEY])
    )
    resp = _preflight(client, "/api/conversations", origin=origin)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", [LOCALHOST_ORIGIN, LOOPBACK_ORIGIN])
def test_localhost_allowed_when_allow_origins_is_set(tmp_path, origin):
    """Regression for OpenHands/OpenHands#8675: explicit allowlist must
    not disable the localhost auto-allow."""
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


# Dispatcher must use the post-``root_path`` route path.


@pytest.mark.asyncio
async def test_dispatcher_matches_workspace_path_after_root_path_strip():
    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    dispatcher = CORSDispatcher(downstream, allow_origins=[])
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


# Unit checks on the path matcher.


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
        "/api/auth/workspace-sessions",
        "/api/conversations",
        "/api/conversations/abc/events",
        "/api/conversations/abc/workspaces/file",
        "/api/auth/login",
        "/",
        "",
    ],
)
def test_is_workspace_cookie_path_rejects(path):
    assert not _is_workspace_cookie_path(path)


# Unit checks on LocalhostCORSMiddleware.is_allowed_origin.


async def _noop_app(scope, receive, send):  # pragma: no cover
    return None


def test_localhost_middleware_localhost_is_unconditional():
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
