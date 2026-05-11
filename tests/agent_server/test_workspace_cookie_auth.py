"""End-to-end tests for the workspace cookie auth flow.

Exercises the full ``create_app(Config(session_api_keys=...))`` wiring so
we cover both the new ``/api/auth/workspace-session`` endpoints and the
cookie-or-header dependency that gates the workspace static-file routes.
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
from openhands.sdk.workspace import LocalWorkspace


SESSION_KEY = "test-key-abc"


@pytest.fixture
def client_factory(tmp_path):
    """Build a TestClient with auth configured and one workspace served."""

    def _build(*, conversation_id: UUID, workspace_dir=None) -> TestClient:
        ws = workspace_dir if workspace_dir is not None else tmp_path

        event_service = AsyncMock(spec=EventService)
        event_service.stored = SimpleNamespace(
            workspace=LocalWorkspace(working_dir=str(ws))
        )
        conversation_service = AsyncMock(spec=ConversationService)

        async def _get_event_service(cid: UUID):
            if cid == conversation_id:
                return event_service
            return None

        conversation_service.get_event_service.side_effect = _get_event_service

        app = create_app(Config(session_api_keys=[SESSION_KEY]))
        # Override the lifespan-managed conversation service with our mock.
        app.dependency_overrides[get_conversation_service] = (
            lambda: conversation_service
        )
        return TestClient(app, raise_server_exceptions=False)

    return _build


@pytest.fixture
def workspace_with_index(tmp_path):
    (tmp_path / "index.html").write_text("<title>hello</title>")
    return tmp_path


def _workspace_url(cid: UUID, path: str = "index.html") -> str:
    return f"/api/conversations/{cid}/workspace/{path}"


# ---- baseline header behavior (regression coverage) -----------------------


def test_workspace_rejects_request_without_credentials(
    client_factory, workspace_with_index
):
    cid = uuid4()
    client = client_factory(conversation_id=cid, workspace_dir=workspace_with_index)

    assert client.get(_workspace_url(cid)).status_code == 401


def test_workspace_accepts_valid_header(client_factory, workspace_with_index):
    cid = uuid4()
    client = client_factory(conversation_id=cid, workspace_dir=workspace_with_index)

    resp = client.get(
        _workspace_url(cid),
        headers={"X-Session-API-Key": SESSION_KEY},
    )
    assert resp.status_code == 200
    assert resp.text == "<title>hello</title>"


def test_workspace_rejects_invalid_header(client_factory, workspace_with_index):
    cid = uuid4()
    client = client_factory(conversation_id=cid, workspace_dir=workspace_with_index)

    resp = client.get(
        _workspace_url(cid),
        headers={"X-Session-API-Key": "not-the-key"},
    )
    assert resp.status_code == 401


# ---- POST /api/auth/workspace-session -------------------------------------


def test_mint_session_requires_header(client_factory, workspace_with_index):
    client = client_factory(conversation_id=uuid4())

    resp = client.post("/api/auth/workspace-session")
    assert resp.status_code == 401
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_mint_session_rejects_bad_header(client_factory):
    client = client_factory(conversation_id=uuid4())

    resp = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_mint_session_returns_cookie_attrs_over_https(client_factory):
    """Behind a TLS-terminating proxy that sets X-Forwarded-Proto=https,
    we issue the full cross-site iframe cookie attribute set."""
    client = client_factory(conversation_id=uuid4())

    resp = client.post(
        "/api/auth/workspace-session",
        headers={
            "X-Session-API-Key": SESSION_KEY,
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "agent.example.com",
        },
    )
    assert resp.status_code == 204

    set_cookie = resp.headers["set-cookie"]
    assert set_cookie.startswith(f"{WORKSPACE_SESSION_COOKIE_NAME}={SESSION_KEY}")
    # Cross-site iframe requirements:
    assert "SameSite=none" in set_cookie
    assert "Secure" in set_cookie
    assert "Partitioned" in set_cookie
    # Defensive defaults:
    assert "HttpOnly" in set_cookie
    assert "Path=/api/conversations" in set_cookie


@pytest.mark.parametrize(
    "host_header",
    [
        "localhost",
        "localhost:8000",
        "127.0.0.1",
        "127.0.0.1:8000",
    ],
)
def test_mint_session_marks_cookie_secure_on_loopback(client_factory, host_header):
    """Browsers (per the Secure Contexts spec) accept ``Secure`` cookies
    on plain-HTTP loopback origins. Issuing Secure here lets local dev
    against ``http://localhost`` actually receive the cookie, which a
    ``SameSite=None`` non-Secure cookie would not."""
    client = client_factory(conversation_id=uuid4())

    resp = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY, "Host": host_header},
    )
    assert resp.status_code == 204

    set_cookie = resp.headers["set-cookie"]
    assert "Secure" in set_cookie
    assert "Partitioned" in set_cookie


def test_mint_session_over_remote_plain_http_drops_secure(client_factory):
    """On non-HTTPS to a non-loopback host we don't claim Secure — the
    browser would reject a Secure cookie over plain HTTP anyway. The
    cookie won't actually work for cross-site embedding in that case
    (SameSite=None requires Secure), but emitting a Secure attribute we
    can't honor would just make the failure mode less obvious."""
    client = client_factory(conversation_id=uuid4())

    resp = client.post(
        "/api/auth/workspace-session",
        headers={
            "X-Session-API-Key": SESSION_KEY,
            "Host": "agent.example.com",
        },
    )
    assert resp.status_code == 204

    set_cookie = resp.headers["set-cookie"]
    assert "SameSite=none" in set_cookie
    assert "Secure" not in set_cookie
    assert "Partitioned" not in set_cookie


# ---- Cookie auth on workspace router --------------------------------------


def test_workspace_accepts_valid_cookie(client_factory, workspace_with_index):
    cid = uuid4()
    client = client_factory(conversation_id=cid, workspace_dir=workspace_with_index)

    mint = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY},
    )
    assert mint.status_code == 204
    assert WORKSPACE_SESSION_COOKIE_NAME in mint.cookies

    # Now fetch with ONLY the cookie -- no X-Session-API-Key header.
    resp = client.get(_workspace_url(cid))
    assert resp.status_code == 200
    assert resp.text == "<title>hello</title>"


def test_workspace_rejects_bogus_cookie(client_factory, workspace_with_index):
    cid = uuid4()
    client = client_factory(conversation_id=cid, workspace_dir=workspace_with_index)

    client.cookies.set(WORKSPACE_SESSION_COOKIE_NAME, "definitely-wrong")
    resp = client.get(_workspace_url(cid))
    assert resp.status_code == 401


# ---- Cookie is rejected by non-workspace endpoints ------------------------


def test_cookie_does_not_authenticate_other_api_endpoints(client_factory):
    """The cookie must only be honored by the workspace router. The rest of
    the API continues to require the X-Session-API-Key header so we don't
    add a CSRF surface to state-changing endpoints."""
    client = client_factory(conversation_id=uuid4())

    mint = client.post(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY},
    )
    assert mint.status_code == 204

    # /api/conversations is gated by the header-only dependency.
    resp = client.get("/api/conversations")
    assert resp.status_code == 401


# ---- DELETE clears the cookie ---------------------------------------------


def test_delete_session_clears_cookie(client_factory):
    client = client_factory(conversation_id=uuid4())

    resp = client.delete(
        "/api/auth/workspace-session",
        headers={"X-Session-API-Key": SESSION_KEY},
    )
    assert resp.status_code == 204
    # Cookie cleared via Max-Age=0 with matching attributes.
    set_cookie = resp.headers["set-cookie"]
    assert f'{WORKSPACE_SESSION_COOKIE_NAME}=""' in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Path=/api/conversations" in set_cookie
