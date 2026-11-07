import json
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openhands.agent_server import conversation_token, mint_conversation_token
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.dependencies import WORKSPACE_SESSION_COOKIE_NAME


SESSION_KEYS = ["key-a", "key-b"]
CONVERSATION_A = UUID("6f1c2b4e-8d3a-4f5b-9c7e-1a2b3c4d5e6f")
CONVERSATION_B = uuid4()
NOT_AUTH_FAILURE = {401, 403}


@pytest.fixture(scope="module")
def client():
    app = create_app(Config(session_api_keys=SESSION_KEYS))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _token(conversation_id=CONVERSATION_A, key="key-a"):
    return mint_conversation_token(key, conversation_id, ttl=300)


def _headers(credential):
    return {"X-Session-API-Key": credential}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/conversations/{id}"),
        ("GET", "/api/conversations/{id}/events/search"),
        ("GET", "/api/conversations/{id}/events/count"),
        ("POST", "/api/conversations/{id}/run"),
        ("POST", "/api/conversations/{id}/condense"),
        ("POST", "/api/conversations/{id}/ask_agent"),
        ("POST", "/api/conversations/{id}/goal"),
        ("GET", "/api/conversations/{id}/file/download"),
        ("GET", "/api/conversations/{id}/git/changes"),
        ("GET", "/api/conversations/{id}/bash/bash_events/search"),
        ("GET", "/api/conversations/{id}/workspace"),
    ],
)
def test_token_reaches_its_own_conversation_routes(client, method, path):
    response = client.request(
        method, path.format(id=CONVERSATION_A), headers=_headers(_token())
    )

    assert response.status_code not in NOT_AUTH_FAILURE


@pytest.mark.parametrize(
    "path",
    [
        "/api/conversations/{id}",
        "/api/conversations/{id}/events/search",
        "/api/conversations/{id}/git/changes",
        "/api/conversations/{id}/workspace",
    ],
)
def test_token_is_forbidden_on_another_conversation(client, path):
    response = client.get(
        path.format(id=CONVERSATION_B), headers=_headers(_token(CONVERSATION_A))
    )

    assert response.status_code == 403


def test_token_is_forbidden_on_uuid_sharing_its_prefix(client):
    sibling = str(CONVERSATION_A)[:-1] + (
        "0" if str(CONVERSATION_A)[-1] != "0" else "1"
    )

    response = client.get(f"/api/conversations/{sibling}", headers=_headers(_token()))

    assert response.status_code == 403


def test_token_cannot_reach_another_conversation_trajectory(client):
    path = (
        f"/api/conversations/{CONVERSATION_A}/file/download-trajectory/{CONVERSATION_B}"
    )

    response = client.get(path, headers=_headers(_token()))

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/settings"),
        ("POST", "/api/bash/execute_bash_command"),
        ("GET", "/api/file/download"),
        ("GET", "/api/file/download-trajectory/{id}"),
        ("GET", "/api/conversations"),
        ("POST", "/api/conversations"),
        ("GET", "/api/conversations/search"),
        ("GET", "/api/conversations/count"),
        ("POST", "/api/hooks"),
        ("GET", "/api/skills/installed"),
        ("POST", "/api/mcp/test"),
        ("POST", "/api/conversations/{id}/bash/execute_bash_command"),
        ("POST", "/api/conversations/{id}/bash/start_bash_command"),
        ("POST", "/api/conversations/{id}/bash/bash_commands/{id}/stop"),
        ("GET", "/api/conversations/{id}/vscode/url"),
        ("GET", "/api/conversations/{id}/vscode/status"),
        ("POST", "/api/auth/workspace-session"),
    ],
)
def test_token_is_forbidden_outside_its_scope(client, method, path):
    response = client.request(
        method, path.format(id=CONVERSATION_A), headers=_headers(_token())
    )

    assert response.status_code == 403


def test_token_signed_with_second_key_is_accepted(client):
    response = client.get(
        f"/api/conversations/{CONVERSATION_A}", headers=_headers(_token(key="key-b"))
    )

    assert response.status_code not in NOT_AUTH_FAILURE


@pytest.mark.parametrize(
    "path", ["/api/conversations/{id}", "/api/conversations/{id}/workspace"]
)
def test_token_from_unconfigured_key_is_unauthorized(client, path):
    response = client.get(
        path.format(id=CONVERSATION_A), headers=_headers(_token(key="retired"))
    )

    assert response.status_code == 401


def test_tampered_token_is_unauthorized(client):
    token = _token()
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    response = client.get(
        f"/api/conversations/{CONVERSATION_A}", headers=_headers(tampered)
    )

    assert response.status_code == 401


def test_expired_token_is_unauthorized(client, monkeypatch):
    monkeypatch.setattr(conversation_token, "time", SimpleNamespace(time=lambda: 1000))
    token = _token()
    monkeypatch.undo()

    response = client.get(
        f"/api/conversations/{CONVERSATION_A}", headers=_headers(token)
    )

    assert response.status_code == 401


def test_token_in_workspace_cookie_is_unauthorized(client):
    client.cookies.set(WORKSPACE_SESSION_COOKIE_NAME, _token())
    try:
        response = client.get(f"/api/conversations/{CONVERSATION_A}/workspace")
    finally:
        client.cookies.clear()

    assert response.status_code == 401


def test_session_key_still_reaches_server_level_routes(client):
    response = client.get("/api/settings", headers=_headers("key-a"))

    assert response.status_code not in NOT_AUTH_FAILURE


@pytest.mark.parametrize("path", ["/server_info", "/health", "/alive"])
def test_unauthenticated_routes_need_no_credential(client, path):
    assert client.get(path).status_code == 200


def test_server_info_advertises_conversation_tokens(client):
    capabilities = client.get("/server_info").json()["capabilities"]

    assert "conversation_token_v1" in capabilities


def _first_frame_close_code(client, path, credential):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(path) as websocket:
            websocket.send_text(
                json.dumps({"type": "auth", "session_api_key": credential})
            )
            while True:
                websocket.receive_text()
    return exc_info.value.code


@pytest.mark.parametrize("path", ["/sockets/session/{id}", "/sockets/events/{id}"])
def test_socket_accepts_token_for_its_conversation(client, path):
    code = _first_frame_close_code(client, path.format(id=CONVERSATION_A), _token())

    assert code not in {4001, 4003}


@pytest.mark.parametrize("path", ["/sockets/session/{id}", "/sockets/events/{id}"])
def test_socket_forbids_token_for_another_conversation(client, path):
    code = _first_frame_close_code(
        client, path.format(id=CONVERSATION_B), _token(CONVERSATION_A)
    )

    assert code == 4003


def test_socket_rejects_expired_token(client, monkeypatch):
    monkeypatch.setattr(conversation_token, "time", SimpleNamespace(time=lambda: 1000))
    token = _token()
    monkeypatch.undo()

    code = _first_frame_close_code(client, f"/sockets/session/{CONVERSATION_A}", token)

    assert code == 4001


def test_bash_events_socket_refuses_tokens(client):
    code = _first_frame_close_code(client, "/sockets/bash-events", _token())

    assert code == 4001


def test_token_in_query_parameter_is_refused(client):
    path = f"/sockets/session/{CONVERSATION_A}?session_api_key={_token()}"

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path) as websocket:
            websocket.receive_text()


def test_rejected_token_is_not_logged(client, caplog):
    token = _token(key="retired")

    with caplog.at_level(logging.DEBUG):
        _first_frame_close_code(client, f"/sockets/session/{CONVERSATION_A}", token)
        client.get(f"/api/conversations/{CONVERSATION_A}", headers=_headers(token))

    assert token not in caplog.text
