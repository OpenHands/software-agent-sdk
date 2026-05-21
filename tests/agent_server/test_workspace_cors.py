"""Tests for the path-scoped workspace CORS allowlist.

The ``allow_workspace_cors_origins`` config field grants CORS access to
the workspace-session auth endpoint and the workspace static-file routes
only — not to the rest of the API. These tests exercise that scoping by
sending preflight (OPTIONS) requests and inspecting the
``access-control-allow-origin`` header on the response.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.dependencies import get_conversation_service
from openhands.agent_server.event_service import EventService
from openhands.sdk.workspace import LocalWorkspace


SESSION_KEY = "test-key-cors"
WORKSPACE_ORIGIN = "https://canvas.example.com"
GLOBAL_ORIGIN = "https://gui.example.com"
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
    """Issue a CORS preflight (OPTIONS) request and return the response."""
    return client.options(
        path,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": request_headers,
        },
    )


@pytest.fixture
def cors_config():
    return Config(
        session_api_keys=[SESSION_KEY],
        allow_cors_origins=[GLOBAL_ORIGIN],
        # Explicitly lock down the workspace allowlist for these tests so we
        # can assert that listed-vs-unlisted origins are differentiated.
        # (The shipped default is ``["*"]`` — see the default-config tests
        # at the bottom of this file.)
        allow_workspace_cors_origins=[WORKSPACE_ORIGIN],
    )


# ---- workspace-session endpoint -------------------------------------------


def test_workspace_session_allows_workspace_scoped_origin(tmp_path, cors_config):
    """Origins listed in allow_workspace_cors_origins get CORS on
    POST /api/auth/workspace-session."""
    client = _build_client(tmp_path, conversation_id=uuid4(), config=cors_config)

    resp = _preflight(client, "/api/auth/workspace-session", origin=WORKSPACE_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == WORKSPACE_ORIGIN
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_workspace_session_allows_global_origin(tmp_path, cors_config):
    """Origins listed in allow_cors_origins also retain CORS on the
    workspace-session endpoint."""
    client = _build_client(tmp_path, conversation_id=uuid4(), config=cors_config)

    resp = _preflight(client, "/api/auth/workspace-session", origin=GLOBAL_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == GLOBAL_ORIGIN


def test_workspace_session_rejects_unlisted_origin(tmp_path, cors_config):
    """An origin in neither list is not granted CORS access."""
    client = _build_client(tmp_path, conversation_id=uuid4(), config=cors_config)

    resp = _preflight(client, "/api/auth/workspace-session", origin=OTHER_ORIGIN)
    # CORSMiddleware returns the preflight with no Allow-Origin header
    # for disallowed origins.
    assert "access-control-allow-origin" not in resp.headers


# ---- workspace static-file routes -----------------------------------------


def test_workspace_static_allows_workspace_scoped_origin(tmp_path, cors_config):
    """Workspace static-file routes also honor the workspace allowlist."""
    cid = uuid4()
    client = _build_client(tmp_path, conversation_id=cid, config=cors_config)

    resp = _preflight(
        client,
        f"/api/conversations/{cid}/workspace/report.html",
        origin=WORKSPACE_ORIGIN,
        method="GET",
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == WORKSPACE_ORIGIN


def test_workspace_static_rejects_unlisted_origin(tmp_path, cors_config):
    cid = uuid4()
    client = _build_client(tmp_path, conversation_id=cid, config=cors_config)

    resp = _preflight(
        client,
        f"/api/conversations/{cid}/workspace/report.html",
        origin=OTHER_ORIGIN,
        method="GET",
    )
    assert "access-control-allow-origin" not in resp.headers


# ---- scoping: workspace allowlist does NOT cover other endpoints ----------


def test_workspace_scoped_origin_denied_on_other_api(tmp_path, cors_config):
    """The workspace-scoped allowlist must not grant CORS access to the
    rest of the API. This is the whole point of having a separate env
    var: a third-party canvas can mint the cookie and load artifacts,
    but cannot read arbitrary endpoints with the user's credentials."""
    client = _build_client(tmp_path, conversation_id=uuid4(), config=cors_config)

    resp = _preflight(client, "/api/conversations", origin=WORKSPACE_ORIGIN)
    assert "access-control-allow-origin" not in resp.headers


def test_global_origin_allowed_on_other_api(tmp_path, cors_config):
    """Sanity check: origins in allow_cors_origins still get CORS on
    non-workspace endpoints."""
    client = _build_client(tmp_path, conversation_id=uuid4(), config=cors_config)

    resp = _preflight(client, "/api/conversations", origin=GLOBAL_ORIGIN)
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == GLOBAL_ORIGIN


# ---- default config: wildcard on workspace routes only --------------------


def test_default_config_allows_any_origin_on_workspace_routes(tmp_path):
    """The shipped default for ``allow_workspace_cors_origins`` is
    ``["*"]``, so workspace-scoped routes accept CORS from any origin
    out of the box.

    Because ``allow_credentials=True``, Starlette echoes the request
    Origin back rather than emitting a literal ``*`` (browsers reject
    ``*`` with credentials)."""
    cid = uuid4()
    client = _build_client(
        tmp_path,
        conversation_id=cid,
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    for path in (
        "/api/auth/workspace-session",
        f"/api/conversations/{cid}/workspace/report.html",
    ):
        resp = _preflight(client, path, origin=OTHER_ORIGIN)
        assert resp.status_code == 200, path
        # With credentials enabled the request origin is echoed back, not "*".
        assert resp.headers["access-control-allow-origin"] == OTHER_ORIGIN, path
        assert resp.headers["access-control-allow-credentials"] == "true", path


def test_default_config_still_blocks_remote_origin_on_other_api(tmp_path):
    """The default workspace wildcard does not bleed into the rest of
    the API — ``allow_cors_origins`` is still empty by default, so
    arbitrary origins are blocked on non-workspace routes."""
    client = _build_client(
        tmp_path,
        conversation_id=uuid4(),
        config=Config(session_api_keys=[SESSION_KEY]),
    )

    resp = _preflight(client, "/api/conversations", origin=OTHER_ORIGIN)
    assert "access-control-allow-origin" not in resp.headers
