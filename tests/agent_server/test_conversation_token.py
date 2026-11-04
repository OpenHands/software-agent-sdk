"""Conversation-scoped tokens: format, scope rules, and the real app wiring."""

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.responses import Response
from starlette.websockets import WebSocketDisconnect

import openhands.agent_server as agent_server
from openhands.agent_server import conversation_token
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_token import (
    conversation_token_allows,
    mint_conversation_token,
    verify_conversation_token,
)
from openhands.agent_server.dependencies import WORKSPACE_SESSION_COOKIE_NAME
from openhands.agent_server.docker_runtime.hybrid import DOCKER_ROUTE_PREFIX


KEY_1 = "session-key-one"
KEY_2 = "session-key-two"
TTL = timedelta(minutes=5)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _token_for_claims(key: str, claims: object) -> str:
    """Build a token by hand from the documented format."""
    payload = _b64(json.dumps(claims).encode())
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()
    return f"oh1.{payload}.{_b64(signature)}"


def _expire_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    later = time.time() + TTL.total_seconds() + 1
    monkeypatch.setattr(conversation_token, "time", SimpleNamespace(time=lambda: later))


# ---- mint / verify ---------------------------------------------------------


def test_mint_and_verify_are_exported_from_the_package():
    assert agent_server.mint_conversation_token is mint_conversation_token
    assert agent_server.verify_conversation_token is verify_conversation_token


def test_minted_token_verifies_with_any_configured_key():
    cid = uuid4()

    token = mint_conversation_token(KEY_2, cid, TTL)

    assert token.startswith("oh1.") and token.count(".") == 2
    assert verify_conversation_token(token, [KEY_1, KEY_2]) == cid


def test_minted_token_matches_documented_format():
    cid = uuid4()

    token = mint_conversation_token(KEY_1, str(cid), TTL)

    _, payload, signature = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["cid"] == str(cid)
    assert time.time() < claims["exp"] <= time.time() + TTL.total_seconds() + 1
    expected = hmac.new(KEY_1.encode(), payload.encode(), hashlib.sha256).digest()
    assert signature == _b64(expected)


def test_token_signed_by_removed_key_is_rejected():
    token = mint_conversation_token(KEY_1, uuid4(), TTL)

    assert verify_conversation_token(token, [KEY_2]) is None


def test_expired_token_is_rejected(monkeypatch):
    token = mint_conversation_token(KEY_1, uuid4(), TTL)

    _expire_tokens(monkeypatch)

    assert verify_conversation_token(token, [KEY_1]) is None


def test_tampered_payload_is_rejected():
    token = mint_conversation_token(KEY_1, uuid4(), TTL)
    _, _, signature = token.split(".")
    forged_payload = _b64(
        json.dumps({"cid": str(uuid4()), "exp": int(time.time()) + 60}).encode()
    )

    assert (
        verify_conversation_token(f"oh1.{forged_payload}.{signature}", [KEY_1]) is None
    )


def test_tampered_signature_is_rejected():
    token = mint_conversation_token(KEY_1, uuid4(), TTL)
    prefix, payload, signature = token.split(".")
    flipped = signature[:-2] + ("AA" if signature[-2:] != "AA" else "BB")

    assert verify_conversation_token(f"{prefix}.{payload}.{flipped}", [KEY_1]) is None


@pytest.mark.parametrize(
    "token",
    ["", KEY_1, "oh1.", "oh1.abc", "oh1.abc.def.ghi", "oh1.not base64!.sig"],
)
def test_malformed_tokens_are_rejected(token):
    assert verify_conversation_token(token, [KEY_1]) is None


def test_token_with_another_version_prefix_is_rejected():
    token = mint_conversation_token(KEY_1, uuid4(), TTL)

    assert verify_conversation_token("oh2." + token[len("oh1.") :], [KEY_1]) is None


@pytest.mark.parametrize(
    "claims",
    [
        ["not", "an", "object"],
        {"exp": 9999999999},
        {"cid": "not-a-uuid", "exp": 9999999999},
        {"cid": str(uuid4()), "exp": True},
        {"cid": str(uuid4()), "exp": "9999999999"},
        {"cid": str(uuid4())},
    ],
)
def test_correctly_signed_bad_claims_are_rejected(claims):
    assert verify_conversation_token(_token_for_claims(KEY_1, claims), [KEY_1]) is None


def test_hand_built_token_in_documented_format_verifies():
    cid = uuid4()
    token = _token_for_claims(KEY_1, {"cid": str(cid), "exp": int(time.time()) + 60})

    assert verify_conversation_token(token, [KEY_1]) == cid


@pytest.mark.parametrize(
    ("key", "ttl"),
    [("", TTL), (KEY_1, timedelta(0)), (KEY_1, timedelta(seconds=-1))],
)
def test_mint_rejects_empty_key_and_non_positive_ttl(key, ttl):
    with pytest.raises(ValueError):
        mint_conversation_token(key, uuid4(), ttl)


# ---- scope rules -----------------------------------------------------------

CID = UUID("8f0c5b1e-3a43-4bd2-9f4e-0e7d3a9c1b2a")
OTHER = UUID("8f0c5b1e-3a43-4bd2-9f4e-0e7d3a9c1b2b")
DOCKER_TAIL = (
    f"/api{DOCKER_ROUTE_PREFIX}/conversations/{{conversation_id}}/{{tail:path}}"
)


@pytest.mark.parametrize(
    ("route", "params"),
    [
        ("/api/conversations/{conversation_id}", {}),
        ("/api/conversations/{conversation_id}/events/search", {}),
        ("/api/conversations/{conversation_id}/run", {}),
        ("/api/conversations/{conversation_id}/workspace/{file_path:path}", {}),
        (
            "/api/conversations/{runtime_conversation_id}/bash/bash_events/search",
            {},
        ),
        ("/api/conversations/{runtime_conversation_id}/file/download", {}),
        (
            "/api/conversations/{runtime_conversation_id}/file/download-trajectory/"
            "{conversation_id}",
            {"conversation_id": str(CID)},
        ),
        ("/sockets/events/{conversation_id}", {}),
        ("/sockets/session/{conversation_id}", {}),
        (f"{DOCKER_ROUTE_PREFIX}/sockets/session/{{conversation_id}}", {}),
        (DOCKER_TAIL, {"tail": "events/search"}),
        (DOCKER_TAIL, {"tail": "bash/bash_events/"}),
    ],
)
def test_scope_allows_own_conversation_routes(route, params):
    id_param = (
        "runtime_conversation_id" if "{runtime_conversation_id}" in route else None
    )
    path_params = {id_param or "conversation_id": str(CID), **params}

    assert conversation_token_allows(route, path_params, CID)


@pytest.mark.parametrize(
    ("route", "params"),
    [
        # Server-level routes, including ones that carry a conversation ID.
        ("/api/settings", {}),
        ("/api/conversations", {}),
        ("/api/conversations/search", {}),
        ("/api/conversations/count", {}),
        ("/api/bash/execute_bash_command", {}),
        ("/api/file/download", {}),
        ("/api/file/download-trajectory/{conversation_id}", {}),
        ("/api/auth/workspace-session", {}),
        ("/api/skills/installed", {}),
        ("/sockets/bash-events", {}),
        # Direct shell and IDE under the token's own conversation.
        ("/api/conversations/{runtime_conversation_id}/bash/execute_bash_command", {}),
        ("/api/conversations/{runtime_conversation_id}/bash/start_bash_command", {}),
        (
            "/api/conversations/{runtime_conversation_id}/bash/bash_commands/"
            "{command_id}/stop",
            {"command_id": "x"},
        ),
        ("/api/conversations/{runtime_conversation_id}/vscode/url", {}),
        ("/api/conversations/{runtime_conversation_id}/vscode/status", {}),
        # A second conversation ID must also match.
        (
            "/api/conversations/{runtime_conversation_id}/file/download-trajectory/"
            "{conversation_id}",
            {"conversation_id": str(OTHER)},
        ),
        # The Docker catch-all is checked against the tail it will proxy.
        ("/api/conversations/{conversation_id}/runtime/credentials", {}),
        (DOCKER_TAIL, {"tail": "bash/execute_bash_command"}),
        (DOCKER_TAIL, {"tail": "bash//execute_bash_command"}),
        (DOCKER_TAIL, {"tail": "./bash/start_bash_command"}),
        (DOCKER_TAIL, {"tail": "bash/bash_commands/abc/stop"}),
        (DOCKER_TAIL, {"tail": "vscode/url"}),
        (DOCKER_TAIL, {"tail": "runtime/credentials"}),
        (DOCKER_TAIL, {"tail": "events/../vscode/url"}),
        (DOCKER_TAIL, {"tail": "bash%2Fexecute_bash_command"}),
        (DOCKER_TAIL, {"tail": "bash\\execute_bash_command"}),
    ],
)
def test_scope_refuses_routes_outside_the_conversation(route, params):
    path_params = {"conversation_id": str(CID), "runtime_conversation_id": str(CID)}
    path_params.update(params)

    assert not conversation_token_allows(route, path_params, CID)


def test_scope_refuses_another_conversation_with_shared_prefix():
    assert not conversation_token_allows(
        "/api/conversations/{conversation_id}",
        {"conversation_id": str(OTHER)},
        CID,
    )


# ---- the real app ----------------------------------------------------------


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    config = Config(
        session_api_keys=[KEY_1, KEY_2],
        conversations_path=tmp_path / "conversations",
        workspace_path=tmp_path / "workspace",
        bash_events_dir=tmp_path / "bash-events",
    )
    app = create_app(config)
    with TestClient(app, raise_server_exceptions=False) as client:
        ids = []
        for name in ("a", "b"):
            workspace = tmp_path / "workspace" / name
            workspace.mkdir(parents=True)
            (workspace / "index.html").write_text(f"<title>{name}</title>")
            response = client.post(
                "/api/conversations",
                headers={"X-Session-API-Key": KEY_1},
                json={
                    "agent": {
                        "llm": {"model": "gpt-4o-mini", "api_key": "test"},
                        "tools": [],
                    },
                    "workspace": {"working_dir": str(workspace)},
                    "autotitle": False,
                },
            )
            assert response.status_code == 201, response.text
            ids.append(response.json()["id"])
        yield SimpleNamespace(
            app=app,
            client=client,
            a=ids[0],
            b=ids[1],
            workspace_a=tmp_path / "workspace" / "a",
            token_a=mint_conversation_token(KEY_2, ids[0], TTL),
        )


def _as(token: str) -> dict[str, str]:
    return {"X-Session-API-Key": token}


def _in_scope_requests(s) -> list[tuple[str, str, dict]]:
    index = s.workspace_a / "index.html"
    return [
        ("GET", f"/api/conversations/{s.a}", {}),
        ("GET", f"/api/conversations/{s.a}/events/search", {}),
        ("GET", f"/api/conversations/{s.a}/events/count", {}),
        (
            "POST",
            f"/api/conversations/{s.a}/events",
            {"json": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        ),
        ("GET", f"/api/conversations/{s.a}/bash/bash_events/search", {}),
        (
            "GET",
            f"/api/conversations/{s.a}/file/download",
            {"params": {"path": str(index)}},
        ),
        (
            "GET",
            f"/api/conversations/{s.a}/git/changes",
            {"params": {"path": str(s.workspace_a)}},
        ),
        ("GET", f"/api/conversations/{s.a}/workspace/index.html", {}),
    ]


def test_token_behaves_like_a_session_key_on_its_conversation(server):
    for method, url, kwargs in _in_scope_requests(server):
        with_key = server.client.request(method, url, headers=_as(KEY_1), **kwargs)
        with_token = server.client.request(
            method, url, headers=_as(server.token_a), **kwargs
        )

        assert with_token.status_code not in (401, 403), (url, with_token.text)
        assert with_token.status_code == with_key.status_code, url


def test_token_reads_workspace_file_from_header(server):
    response = server.client.get(
        f"/api/conversations/{server.a}/workspace/index.html",
        headers=_as(server.token_a),
    )

    assert response.status_code == 200
    assert response.text == "<title>a</title>"


def test_token_is_forbidden_on_another_conversation(server):
    token_b = mint_conversation_token(KEY_1, server.b, TTL)
    for headers, url in [
        (_as(server.token_a), f"/api/conversations/{server.b}"),
        (_as(server.token_a), f"/api/conversations/{server.b}/events/search"),
        (_as(server.token_a), f"/api/conversations/{server.b}/bash/bash_events/search"),
        (_as(token_b), f"/api/conversations/{server.a}/workspace/index.html"),
    ]:
        assert server.client.get(url, headers=headers).status_code == 403, url


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "/api/settings"),
        ("POST", "/api/bash/execute_bash_command"),
        ("GET", "/api/file/download"),
        ("GET", "/api/conversations"),
        ("POST", "/api/conversations"),
        ("GET", "/api/conversations/search"),
        ("GET", "/api/conversations/count"),
        ("GET", "/api/skills/installed"),
        ("POST", "/api/hooks"),
        ("POST", "/api/mcp/test"),
        ("GET", "/api/file/download-trajectory/{a}"),
        ("POST", "/api/conversations/{a}/bash/execute_bash_command"),
        ("POST", "/api/conversations/{a}/bash/start_bash_command"),
        ("POST", "/api/conversations/{a}/bash/bash_commands/{a}/stop"),
        ("GET", "/api/conversations/{a}/vscode/url"),
        ("GET", "/api/conversations/{a}/vscode/status"),
    ],
)
def test_token_is_forbidden_outside_its_conversation_routes(server, method, url):
    response = server.client.request(
        method, url.format(a=server.a), headers=_as(server.token_a)
    )

    assert response.status_code == 403


def test_token_cannot_mint_the_workspace_cookie(server):
    response = server.client.post(
        "/api/auth/workspace-session", headers=_as(server.token_a)
    )

    assert response.status_code == 403
    assert "set-cookie" not in {name.lower() for name in response.headers}


def test_workspace_cookie_minted_by_session_key_still_works(server):
    minted = server.client.post("/api/auth/workspace-session", headers=_as(KEY_1))
    assert minted.status_code == 204
    assert server.client.cookies.get(WORKSPACE_SESSION_COOKIE_NAME) == KEY_1

    response = server.client.get(f"/api/conversations/{server.a}/workspace/index.html")

    assert response.status_code == 200


def test_token_in_workspace_cookie_is_not_accepted(server):
    server.client.cookies.set(WORKSPACE_SESSION_COOKIE_NAME, server.token_a)

    response = server.client.get(f"/api/conversations/{server.a}/workspace/index.html")

    assert response.status_code == 401


def test_invalid_tokens_are_unauthorized(server, monkeypatch):
    url = f"/api/conversations/{server.a}"
    _, payload, signature = server.token_a.split(".")
    tampered = f"oh1.{payload}.{signature[::-1]}"
    unknown_key = mint_conversation_token("not-configured", server.a, TTL)
    for token in (tampered, unknown_key, "oh1.garbage"):
        assert server.client.get(url, headers=_as(token)).status_code == 401

    _expire_tokens(monkeypatch)
    assert server.client.get(url, headers=_as(server.token_a)).status_code == 401


def test_rotating_out_the_signing_key_revokes_its_tokens(server):
    url = f"/api/conversations/{server.a}"
    assert server.client.get(url, headers=_as(server.token_a)).status_code == 200

    server.app.state.config = server.app.state.config.model_copy(
        update={"session_api_keys": [KEY_1]}
    )

    assert server.client.get(url, headers=_as(server.token_a)).status_code == 401


def test_session_keys_still_reach_server_level_routes(server):
    for key in (KEY_1, KEY_2):
        assert server.client.get("/api/settings", headers=_as(key)).status_code == 200
        assert (
            server.client.get("/api/conversations/search", headers=_as(key)).status_code
            == 200
        )


def test_unauthenticated_routes_are_unchanged(server):
    for url in ("/server_info", "/health", "/alive"):
        assert server.client.get(url).status_code == 200, url

    capabilities = server.client.get("/server_info").json()["capabilities"]
    assert "conversation_token_v1" in capabilities


def _first_frame(token: str) -> str:
    return json.dumps({"type": "auth", "session_api_key": token})


def test_token_opens_its_session_socket_with_first_frame_auth(server):
    with server.client.websocket_connect(f"/sockets/session/{server.a}") as ws:
        ws.send_text(_first_frame(server.token_a))

        assert json.loads(ws.receive_text())["type"] == "sync"


def test_token_opens_its_events_socket_with_first_frame_auth(server):
    url = f"/sockets/events/{server.a}?resend_mode=all"
    with server.client.websocket_connect(url) as ws:
        ws.send_text(_first_frame(server.token_a))

        assert "kind" in json.loads(ws.receive_text())


@pytest.mark.parametrize(
    ("url", "token_for", "code"),
    [
        ("/sockets/session/{b}", "a", 4003),
        ("/sockets/events/{b}", "a", 4003),
        ("/sockets/session/{a}", "expired", 4001),
        ("/sockets/bash-events", "a", 4001),
    ],
)
def test_socket_rejects_token(server, monkeypatch, url, token_for, code):
    if token_for == "expired":
        _expire_tokens(monkeypatch)

    with server.client.websocket_connect(url.format(a=server.a, b=server.b)) as ws:
        ws.send_text(_first_frame(server.token_a))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()

    assert exc_info.value.code == code


def test_token_is_never_logged(server, caplog):
    caplog.set_level(logging.DEBUG)
    server.client.get(f"/api/conversations/{server.a}", headers=_as(server.token_a))
    server.client.get(f"/api/conversations/{server.b}", headers=_as(server.token_a))
    server.client.get("/api/settings", headers=_as(server.token_a))
    with server.client.websocket_connect(f"/sockets/session/{server.b}") as ws:
        ws.send_text(_first_frame(server.token_a))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    assert "conversation token outside its scope" in caplog.text
    assert server.token_a not in caplog.text
    for key in (KEY_1, KEY_2):
        assert key not in caplog.text


def test_docker_runtime_proxy_refuses_shell_ide_and_runtime_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    app = create_app(
        Config(
            conversation_runtime="docker",
            session_api_keys=[KEY_1],
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
        )
    )
    app.state.conversation_service = AsyncMock()
    proxied: list[str] = []

    async def container(*_args):
        return SimpleNamespace(host="http://inner", api_key="inner-key")

    async def proxy(*_args, upstream_path, **_kwargs):
        proxied.append(upstream_path)
        return Response()

    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers._container", container
    )
    monkeypatch.setattr(
        "openhands.agent_server.docker_runtime.routers.proxy_http", proxy
    )
    cid = uuid4()
    client = TestClient(app, raise_server_exceptions=False)
    headers = _as(mint_conversation_token(KEY_1, cid, TTL))

    for method, tail in [
        ("POST", "runtime/credentials"),
        ("POST", "bash/execute_bash_command"),
        ("POST", "bash//start_bash_command"),
        ("GET", "vscode/url"),
    ]:
        url = f"/api/conversations/{cid}/{tail}"
        assert client.request(method, url, headers=headers).status_code == 403, tail
    assert proxied == []

    response = client.post(f"/api/conversations/{cid}/run", headers=headers)

    assert response.status_code == 200
    assert proxied == [f"/api/conversations/{cid}/run"]
