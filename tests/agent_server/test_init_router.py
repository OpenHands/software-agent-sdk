"""Tests for the deferred-init / dormant-mode flow.

Background: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import io
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request, Response, status
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from pydantic import SecretStr

from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import CONFIG_PATH_ENV, Config, load_config
from openhands.agent_server.init_router import (
    InitRequest,
    InitService,
    InitState,
    _build_initialized_config,
    require_initialized,
)
from openhands.agent_server.server_context import (
    build_server_context,
    install_server_context,
    server_context_of,
)
from openhands.agent_server.vscode_service import VSCodeService


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The agent-server pulls config from env at import time in places;
    null these out so each test starts from a clean slate."""
    for key in (
        "OH_DEFERRED_INIT",
        "OH_WEB_URL",
        "RUNTIME_URL",
        "TMUX_TMPDIR",
        "SESSION_API_KEY",
        "OH_SESSION_API_KEYS_0",
        "OH_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _fresh_vscode_service(monkeypatch):
    """/api/init updates the VSCode service singleton; give each test its own."""
    monkeypatch.setattr("openhands.agent_server.vscode_service._vscode_service", None)


def _dormant_app(config: Config) -> FastAPI:
    """A bare app holding the dormant server context, as ``create_app`` leaves it."""
    app = FastAPI()
    install_server_context(app, build_server_context(config))
    return app


def _vscode_url_after_init(tmp_path: Path) -> str | None:
    """Boot a dormant app, deliver a session key through /api/init, and read
    /api/vscode/url with that key."""
    cfg = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )
    with TestClient(create_app(cfg)) as client:
        resp = client.post(
            "/api/init",
            json={
                "session_api_keys": ["user-session-key"],
                "conversations_path": str(tmp_path / "u" / "convs"),
                "bash_events_dir": str(tmp_path / "u" / "bash"),
            },
        )
        assert resp.status_code == 200

        resp = client.get(
            "/api/vscode/url", headers={"X-Session-API-Key": "user-session-key"}
        )
        assert resp.status_code == 200
        return resp.json()["url"]


class TestConfigDefaults:
    def test_deferred_init_defaults_false(self):
        assert Config().deferred_init is False


class TestBuildInitializedConfig:
    def test_clears_deferred_init_flag(self):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(base, InitRequest())
        assert merged.deferred_init is False

    def test_overrides_only_provided_fields(self, tmp_path):
        base = Config(
            deferred_init=True,
            conversations_path=Path("base/convs"),
            bash_events_dir=Path("base/bash"),
            max_concurrent_runs=5,
        )
        req = InitRequest(
            session_api_keys=["k1"],
            conversations_path=tmp_path / "user-workspace" / "conversations",
        )
        merged = _build_initialized_config(base, req)
        assert merged.session_api_keys == ["k1"]
        assert (
            merged.conversations_path == tmp_path / "user-workspace" / "conversations"
        )
        # Untouched fields keep base values.
        assert merged.bash_events_dir == Path("base/bash")
        assert merged.max_concurrent_runs == 5

    def test_secret_key_falls_back_to_session_key(self):
        base = Config(deferred_init=True)
        # base.secret_key default is None (no env), so we should fall back
        # to the first session key after /api/init.
        assert base.secret_key is None
        merged = _build_initialized_config(
            base, InitRequest(session_api_keys=["s1", "s2"])
        )
        assert merged.secret_key is not None
        assert merged.secret_key.get_secret_value() == "s1"

    def test_previous_secret_keys_reach_the_cipher(self):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(
            base,
            InitRequest(
                session_api_keys=["new-session-key"],
                previous_secret_keys=[SecretStr("old-session-key")],
            ),
        )
        assert merged.cipher is not None
        assert merged.cipher.secret_key == "new-session-key"
        assert merged.cipher.previous_keys == ("old-session-key",)

    def test_explicit_secret_key_wins(self):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(
            base,
            InitRequest(
                session_api_keys=["sk"], secret_key=SecretStr("explicit-secret")
            ),
        )
        assert merged.secret_key is not None
        assert merged.secret_key.get_secret_value() == "explicit-secret"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_empty_secret_key_is_treated_as_unset(self, blank):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(
            base, InitRequest(session_api_keys=["s1"], secret_key=SecretStr(blank))
        )
        assert merged.secret_key is not None
        assert merged.secret_key.get_secret_value() == "s1"


class TestRouterMounting:
    """Behavior of the /api/init endpoint outside the lifespan."""

    def test_init_get_404_without_deferred_mode(self):
        # When deferred_init=False the InitService is never attached to
        # app.state, so the endpoint behaves as if not configured.
        app = create_app(Config(deferred_init=False))
        client = TestClient(app)
        resp = client.get("/api/init")
        assert resp.status_code == 404


class TestInitServiceTransitions:
    @pytest.mark.asyncio
    async def test_init_transitions_dormant_to_ready(self, tmp_path):
        from openhands.agent_server.bash_service import BashEventService

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)
        assert svc.state == "dormant"

        result = await svc.initialize(
            InitRequest(
                session_api_keys=["user-key"],
                conversations_path=tmp_path / "user" / "convs",
                bash_events_dir=tmp_path / "user" / "bash",
            )
        )
        try:
            assert result.state == "ready"
            assert svc.state == "ready"
            # The new context carries the delivered config with
            # deferred_init cleared.
            context = server_context_of(app)
            assert context.config.deferred_init is False
            assert context.config.session_api_keys == ["user-key"]
            assert context.conversation_service is not None
            # Its BashEventService is the one the bash websocket handler
            # picks up, so it uses the per-user bash_events_dir rather than
            # the dormant one.
            assert isinstance(context.bash_event_service, BashEventService)
            assert (
                context.bash_event_service.bash_events_dir == tmp_path / "user" / "bash"
            )
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    async def test_second_init_rejected_with_400(self, tmp_path):
        from fastapi import HTTPException

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u1" / "convs",
                bash_events_dir=tmp_path / "u1" / "bash",
            )
        )
        try:
            with pytest.raises(HTTPException) as excinfo:
                await svc.initialize(InitRequest())
            assert excinfo.value.status_code == 400
            assert "already in state" in str(excinfo.value.detail)
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    async def test_init_applies_env_vars(self, tmp_path, monkeypatch):
        # Pre-clean so the env var truly comes from /api/init.
        monkeypatch.delenv("DEFERRED_INIT_TEST_VAR", raising=False)
        observed_env = None

        def capture_observability_env():
            nonlocal observed_env
            observed_env = os.environ.get("DEFERRED_INIT_TEST_VAR")

        monkeypatch.setattr(
            "openhands.agent_server.init_router.maybe_init_laminar",
            capture_observability_env,
        )
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        await svc.initialize(
            InitRequest(
                env={"DEFERRED_INIT_TEST_VAR": "hello"},
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        try:
            assert os.environ.get("DEFERRED_INIT_TEST_VAR") == "hello"
            assert observed_env == "hello"
        finally:
            await svc.teardown()
            monkeypatch.delenv("DEFERRED_INIT_TEST_VAR", raising=False)

    @pytest.mark.asyncio
    async def test_init_bash_service_uses_user_supplied_dir(self, tmp_path):
        """After /api/init, the context's bash service uses the
        bash_events_dir supplied in the InitRequest."""
        from openhands.agent_server.bash_service import BashEventService

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        user_dir = tmp_path / "user" / "bash"
        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "user" / "convs",
                bash_events_dir=user_dir,
            )
        )
        try:
            bash_svc = server_context_of(app).bash_event_service
            assert isinstance(bash_svc, BashEventService)
            assert bash_svc.bash_events_dir == user_dir
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    async def test_init_teardown_releases_bash_service(self, tmp_path):
        """When /api/init is followed by teardown, the bash service must also
        be exited so its background tasks are released."""
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        bash_svc = server_context_of(app).bash_event_service
        assert bash_svc is not None and not bash_svc._closed
        await svc.teardown()
        assert bash_svc._closed

    @pytest.mark.asyncio
    async def test_init_switches_vscode_to_first_session_key(
        self, tmp_path, monkeypatch
    ):
        """VSCode booted with a random token; init switches it to the first
        session key, which a server that booted with the key would use."""
        vscode = SimpleNamespace(set_connection_token=AsyncMock())
        monkeypatch.setattr(
            "openhands.agent_server.init_router.get_vscode_service",
            lambda _config: vscode,
        )
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        await svc.initialize(
            InitRequest(
                session_api_keys=["user-key", "second-key"],
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        try:
            vscode.set_connection_token.assert_awaited_once_with("user-key")
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    async def test_init_without_session_keys_keeps_vscode_token(
        self, tmp_path, monkeypatch
    ):
        """With no session key to switch to, VSCode keeps its boot token."""
        vscode = SimpleNamespace(set_connection_token=AsyncMock())
        monkeypatch.setattr(
            "openhands.agent_server.init_router.get_vscode_service",
            lambda _config: vscode,
        )
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)

        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        try:
            vscode.set_connection_token.assert_not_awaited()
        finally:
            await svc.teardown()


class TestEndToEndOverLifespan:
    """Drive the whole flow through the FastAPI lifespan + TestClient."""

    def test_blank_init_secret_falls_back_to_session_key(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            response = client.post(
                "/api/init",
                json={
                    "secret_key": "",
                    "session_api_keys": ["user-session-key"],
                },
            )
            assert response.status_code == 200
            cipher = server_context_of(app).config.cipher
            assert cipher is not None
            assert cipher.secret_key == "user-session-key"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_secret_key_env_keeps_init_closed(self, tmp_path, monkeypatch, blank):
        monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "missing.json"))
        monkeypatch.setenv("OH_SECRET_KEY", blank)
        monkeypatch.setenv("OH_DEFERRED_INIT", "true")
        monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(tmp_path / "convs"))
        monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(tmp_path / "bash"))
        cfg = load_config()
        assert cfg.deferred_init and cfg.secret_key is None
        with TestClient(create_app(cfg)) as client:
            assert client.post("/api/init", json={}).status_code == 401
            assert client.get("/api/init").json()["state"] == "dormant"

    @pytest.mark.parametrize("session_api_keys", [[""], ["", "user-session-key"]])
    def test_init_rejects_empty_session_keys(self, tmp_path, session_api_keys):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            response = client.post(
                "/api/init", json={"session_api_keys": session_api_keys}
            )
            assert response.status_code == 422
            assert "user-session-key" not in response.text
            assert client.get("/api/init").json()["state"] == "dormant"

            response = client.post(
                "/api/init", json={"session_api_keys": ["user-session-key"]}
            )
            assert response.status_code == 200
            response = client.get(
                "/api/conversations/count",
                headers={"X-Session-API-Key": "user-session-key"},
            )
            assert response.status_code == 200

    @pytest.mark.parametrize(
        ("method", "path"),
        (
            ("GET", "/api/conversations/count"),
            ("GET", "/v1/models"),
            (
                "GET",
                "/api/conversations/00000000-0000-0000-0000-000000000000/"
                "workspace/index.html",
            ),
            ("POST", "/app-backends/test-extension/session"),
        ),
    )
    def test_dormant_readiness_precedes_authentication(
        self, tmp_path: Path, method: str, path: str
    ) -> None:
        cfg = Config(
            deferred_init=True,
            session_api_keys=["dormant-key"],
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            assert client.request(method, path).status_code == 503

    @pytest.mark.parametrize("init_state", ("dormant", "initializing"))
    def test_app_backend_http_requires_ready_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        init_state: InitState,
    ) -> None:
        async def accept_app_backend_http(
            _request: Request, _extension_name: str, _path: str
        ) -> Response:
            return Response(status_code=200)

        monkeypatch.setattr(
            "openhands.agent_server.canvas_extensions_bridge_router."
            "proxy_app_backend_http",
            accept_app_backend_http,
        )
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            init_service: InitService = app.state.init_service
            init_service._state = init_state
            assert client.get("/api/init").json()["state"] == init_state
            assert client.get("/app-backends/test-extension").status_code == 503

    @pytest.mark.parametrize(
        (
            "path",
            "dormant_keys",
            "request_headers",
            "expected_status",
            "ready_headers",
        ),
        (
            (
                "/api/conversations/count",
                [],
                {},
                401,
                {"X-Session-API-Key": "ready-key"},
            ),
            (
                "/api/conversations/count",
                ["dormant-key"],
                {"X-Session-API-Key": "dormant-key"},
                401,
                {"X-Session-API-Key": "ready-key"},
            ),
            (
                "/api/conversations/count",
                [],
                {"X-Session-API-Key": "ready-key"},
                200,
                {"X-Session-API-Key": "ready-key"},
            ),
            (
                "/v1/models",
                [],
                {},
                401,
                {"Authorization": "Bearer ready-key"},
            ),
            (
                "/v1/models",
                ["dormant-key"],
                {"Authorization": "Bearer dormant-key"},
                401,
                {"Authorization": "Bearer ready-key"},
            ),
            (
                "/v1/models",
                [],
                {"Authorization": "Bearer ready-key"},
                200,
                {"Authorization": "Bearer ready-key"},
            ),
        ),
    )
    def test_request_crossing_init_uses_ready_credentials(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        dormant_keys: list[str],
        request_headers: dict[str, str],
        expected_status: int,
        ready_headers: dict[str, str],
    ) -> None:
        cfg = Config(
            deferred_init=True,
            session_api_keys=dormant_keys,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        readiness_reached = Event()
        continue_request = Event()

        def pause_at_readiness(request: Request) -> None:
            readiness_reached.set()
            assert continue_request.wait(timeout=5)
            require_initialized(request)

        app.dependency_overrides[require_initialized] = pause_at_readiness
        with TestClient(app) as client:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    client.get,
                    path,
                    headers=request_headers,
                )
                try:
                    assert readiness_reached.wait(timeout=5)
                    initialized = client.post(
                        "/api/init",
                        json={
                            "session_api_keys": ["ready-key"],
                            "conversations_path": str(tmp_path / "u" / "convs"),
                            "bash_events_dir": str(tmp_path / "u" / "bash"),
                        },
                    )
                    assert initialized.status_code == 200
                finally:
                    continue_request.set()
                crossed = pending.result(timeout=5)

            assert crossed.status_code == expected_status
            assert client.get(path, headers=ready_headers).status_code == 200

    def test_dormant_503s_api_routes_until_init(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            # Health/ready/server_info are not gated.
            assert client.get("/alive").status_code == 200
            assert client.get("/ready").status_code == 200

            # Sample /api/* route — should be 503. The agent-server's
            # 5xx exception handler replaces ``detail`` with a generic
            # "Internal Server Error" message, so we only assert on the
            # status code here — that's what the warm-pool orchestrator
            # actually inspects.
            resp = client.get("/api/conversations/count")
            assert resp.status_code == 503

            # Init status reports dormant.
            resp = client.get("/api/init")
            assert resp.status_code == 200
            assert resp.json()["state"] == "dormant"

            # Run /api/init.
            resp = client.post(
                "/api/init",
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200
            assert resp.json()["state"] == "ready"

            # /api/* now works (200, not 503).
            resp = client.get("/api/conversations/count")
            assert resp.status_code == 200

    def test_dormant_refuses_bash_socket_until_init(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        marker = tmp_path / "marker"
        with TestClient(create_app(cfg)) as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect("/sockets/bash-events") as ws:
                    ws.send_json({"command": f"touch {marker}"})
            assert excinfo.value.code == status.WS_1013_TRY_AGAIN_LATER
            assert not marker.exists()

            resp = client.post(
                "/api/init",
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            with client.websocket_connect("/sockets/bash-events"):
                pass

    def test_dormant_503s_openai_routes_until_init(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            assert client.get("/v1/models").status_code == 503

            resp = client.post(
                "/api/init",
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            assert client.get("/v1/models").status_code == 200

    def test_root_path_updates_from_init_web_url(self, tmp_path):
        """When /api/init delivers a ``web_url``, the FastAPI ``root_path``
        must be re-derived from it so OpenAPI/Swagger/ReDoc URLs reflect the
        external mount path (e.g. behind a reverse proxy)."""
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            # Dormant server has no web_url → empty root_path.
            assert app.root_path == ""

            resp = client.post(
                "/api/init",
                json={
                    "web_url": "https://example.com/agent-server-123/agent-server/",
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            # The FastAPI app's root_path must now match the prefix from
            # the per-user web_url so OpenAPI doc URLs and Swagger asset
            # links are correct under the reverse-proxy mount path.
            assert app.root_path == "/agent-server-123/agent-server", (
                f"root_path was not updated after /api/init: {app.root_path!r}"
            )

    def test_init_api_key_required_when_configured(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            secret_key=SecretStr("pool-key"),
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            # Wrong key → 401.
            resp = client.post(
                "/api/init",
                headers={"X-Init-API-Key": "wrong"},
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 401

            # No key → 401.
            resp = client.post("/api/init", json={})
            assert resp.status_code == 401

            # Right key → 200.
            resp = client.post(
                "/api/init",
                headers={"X-Init-API-Key": "pool-key"},
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            # GET /api/init does NOT require the key (status polling).
            resp = client.get("/api/init")
            assert resp.status_code == 200

    def test_session_api_key_set_at_init_protects_api(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            # Before /api/init, no session key required at startup config
            # level — but the dormant gate 503s anyway.
            assert client.get("/api/conversations/count").status_code == 503

            # Init delivers the session key.
            resp = client.post(
                "/api/init",
                json={
                    "session_api_keys": ["user-session-key"],
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            assert server_context_of(app).config.session_api_keys == [
                "user-session-key"
            ]
            assert client.get("/api/conversations/count").status_code == 401
            assert (
                client.get(
                    "/api/conversations/count",
                    headers={"X-Session-API-Key": "wrong-key"},
                ).status_code
                == 401
            )
            assert (
                client.get(
                    "/api/conversations/count",
                    headers={"X-Session-API-Key": "user-session-key"},
                ).status_code
                == 200
            )

    def test_download_trajectory_uses_init_conversations_path(self, tmp_path):
        """Both trajectory routes serve conversations stored under the
        ``conversations_path`` delivered by /api/init, not the launch path."""
        init_convs = tmp_path / "u" / "convs"
        workspace = tmp_path / "ws"
        workspace.mkdir()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            resp = client.post(
                "/api/init",
                json={
                    "conversations_path": str(init_convs),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200

            resp = client.post(
                "/api/conversations",
                json={
                    "agent": {
                        "kind": "Agent",
                        "llm": {"model": "test", "api_key": "sk-init-path"},
                        "tools": [],
                    },
                    "workspace": {"working_dir": str(workspace)},
                },
            )
            assert resp.status_code in (200, 201), resp.text
            conversation_id = UUID(resp.json()["id"])
            assert (init_convs / conversation_id.hex / "base_state.json").exists()

            for route in (
                f"/api/file/download-trajectory/{conversation_id}",
                f"/api/conversations/{conversation_id}"
                f"/file/download-trajectory/{conversation_id}",
            ):
                resp = client.get(route)
                assert resp.status_code == 200, (route, resp.text)
                with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
                    names = archive.namelist()
                    blob = b"".join(archive.read(n) for n in names)
                assert f"{conversation_id.hex}/base_state.json" in names
                assert b"sk-init-path" not in blob
                assert not list(init_convs.glob("*.zip"))

            missing = uuid4()
            resp = client.get(f"/api/file/download-trajectory/{missing}")
            assert resp.status_code == 404
            assert resp.json()["detail"] == "Conversation not found"

    def test_vscode_url_uses_session_key_after_init(self, tmp_path, monkeypatch):
        """After /api/init the VSCode URL's token is the first session key, so
        an orchestrator holding the key can build the URL itself."""

        async def fake_start(service):
            # Like start(): a server booted without a session key makes up a
            # random token.
            service.connection_token = service.connection_token or "boot-token"
            return True

        monkeypatch.setattr(VSCodeService, "start", fake_start)
        monkeypatch.setattr(VSCodeService, "stop", AsyncMock())
        monkeypatch.setattr(VSCodeService, "is_running", lambda service: True)

        url = _vscode_url_after_init(tmp_path)

        assert url is not None
        assert "?tkn=user-session-key&" in url

    def test_vscode_url_stays_empty_without_vscode(self, tmp_path, monkeypatch):
        """Where VSCode never started, as in images that don't ship it,
        /api/vscode/url still reports no URL after /api/init."""
        monkeypatch.setattr(
            VSCodeService, "_check_vscode_available", lambda service: False
        )

        assert _vscode_url_after_init(tmp_path) is None


class TestNonDeferredPathUnchanged:
    """Regression: deferred_init=False must behave exactly like before."""

    def test_non_deferred_does_not_create_init_service(self, tmp_path):
        cfg = Config(
            deferred_init=False,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            # No init_service in non-deferred mode.
            assert getattr(app.state, "init_service", None) is None
            # /api/* should be live (200) — the dormant gate is a no-op.
            assert client.get("/api/conversations/count").status_code == 200
            # /api/init returns 404 because no InitService is attached.
            assert client.get("/api/init").status_code == 404


@pytest.mark.asyncio
async def test_lifespan_teardown_releases_conversation_service_after_init(
    tmp_path,
):
    """If /api/init succeeds, the lifespan finally clause must release the
    conversation service. If /api/init never runs, teardown is a no-op."""
    cfg = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )
    # A bare app is enough: api_lifespan only reads its server context.
    app = _dormant_app(cfg)
    async with api_lifespan(app):
        init_svc = app.state.init_service
        assert init_svc.state == "dormant"
        await init_svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        assert init_svc.state == "ready"
        context = server_context_of(app)
        assert context.conversation_service is not None
        assert context.bash_event_service is not None
    # After lifespan exit the conversation service should have been torn
    # down, and the bash service with it.
    assert init_svc._started is None
    assert context.conversation_service._event_services is None
    assert context.bash_event_service._closed


class TestSessionKeyExport:
    """The key the server authenticates with reaches in-process callers that
    read it from the environment (start_child_conversation, managed LLM key
    refresh), whether it arrived through /api/init or a config file."""

    def test_init_exports_delivered_key_over_boot_key(self, tmp_path, monkeypatch):
        """A dormant pod boots holding a shared key; /api/init replaces it."""
        monkeypatch.setenv("OH_SESSION_API_KEYS_0", "boot-key")
        cfg = Config(
            deferred_init=True,
            session_api_keys=["boot-key"],
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            resp = client.post(
                "/api/init",
                json={
                    "session_api_keys": ["user-session-key"],
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
                # The bootstrap credential is the dormant secret key, which
                # defaults to the first session key.
                headers={"X-Init-API-Key": "boot-key"},
            )
            assert resp.status_code == 200
            assert os.environ["OH_SESSION_API_KEYS_0"] == "user-session-key"

    @pytest.mark.asyncio
    async def test_teardown_restores_the_env_after_a_direct_init(self, tmp_path):
        """Driving InitService without the lifespan (as other tests do) must
        not leak the delivered key into the process environment."""
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = _dormant_app(base)
        svc = InitService(app, base_config=base)
        try:
            await svc.initialize(
                InitRequest(
                    session_api_keys=["user-key"],
                    conversations_path=tmp_path / "user" / "convs",
                    bash_events_dir=tmp_path / "user" / "bash",
                )
            )
            assert os.environ["OH_SESSION_API_KEYS_0"] == "user-key"
        finally:
            await svc.teardown()
        assert "OH_SESSION_API_KEYS_0" not in os.environ

    def test_init_without_keys_leaves_env_untouched(self, tmp_path):
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)) as client:
            resp = client.post(
                "/api/init",
                json={
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                },
            )
            assert resp.status_code == 200
            assert "OH_SESSION_API_KEYS_0" not in os.environ

    def test_startup_exports_config_key_and_restores_env_on_shutdown(self, tmp_path):
        """A key read from the config file is exported for the server's
        lifetime only."""
        cfg = Config(
            deferred_init=False,
            session_api_keys=["file-key"],
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        with TestClient(create_app(cfg)):
            assert os.environ["OH_SESSION_API_KEYS_0"] == "file-key"
        assert "OH_SESSION_API_KEYS_0" not in os.environ
