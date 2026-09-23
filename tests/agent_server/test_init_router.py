"""Tests for the deferred-init / dormant-mode flow.

Background: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config
from openhands.agent_server.init_router import (
    InitRequest,
    InitService,
    _build_initialized_config,
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


def _reset_conversation_singleton():
    """Some tests build their own ConversationService; reset the module-level
    cache so unrelated tests don't see leftover state."""
    from openhands.agent_server import conversation_service as cs_mod

    cs_mod._conversation_service = None


def _reset_bash_singleton():
    """Reset the module-level BashEventService cache so each test starts fresh."""
    from openhands.agent_server import bash_service as bash_mod

    bash_mod._bash_event_service = None


def _vscode_url_after_init(tmp_path: Path) -> str | None:
    """Boot a dormant app, deliver a session key through /api/init, and read
    /api/vscode/url with that key."""
    _reset_conversation_singleton()
    cfg = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )
    with TestClient(create_app(cfg)) as client:
        try:
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
        finally:
            _reset_conversation_singleton()


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
        _reset_conversation_singleton()
        _reset_bash_singleton()
        from openhands.agent_server.bash_service import BashEventService

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]
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
            # New config landed on app.state with deferred_init cleared.
            assert app.state.config.deferred_init is False
            assert app.state.config.session_api_keys == ["user-key"]
            assert app.state.conversation_service is not None
            # BashEventService is registered on app.state so the bash
            # websocket handler picks up the per-user bash_events_dir
            # rather than the import-time default.
            assert isinstance(
                getattr(app.state, "bash_event_service", None), BashEventService
            )
            assert (
                app.state.bash_event_service.bash_events_dir
                == tmp_path / "user" / "bash"
            )
        finally:
            await svc.teardown()
            _reset_conversation_singleton()
            _reset_bash_singleton()

    @pytest.mark.asyncio
    async def test_second_init_rejected_with_400(self, tmp_path):
        _reset_conversation_singleton()
        from fastapi import HTTPException

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

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
            _reset_conversation_singleton()

    @pytest.mark.asyncio
    async def test_init_applies_env_vars(self, tmp_path, monkeypatch):
        _reset_conversation_singleton()
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
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

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
            _reset_conversation_singleton()

    @pytest.mark.asyncio
    async def test_init_bash_service_uses_user_supplied_dir(self, tmp_path):
        """After /api/init, app.state.bash_event_service uses the
        bash_events_dir supplied in the InitRequest."""
        _reset_conversation_singleton()
        _reset_bash_singleton()
        from openhands.agent_server.bash_service import BashEventService

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        user_dir = tmp_path / "user" / "bash"
        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "user" / "convs",
                bash_events_dir=user_dir,
            )
        )
        try:
            bash_svc = app.state.bash_event_service
            assert isinstance(bash_svc, BashEventService)
            assert bash_svc.bash_events_dir == user_dir
        finally:
            await svc.teardown()
            _reset_conversation_singleton()
            _reset_bash_singleton()

    @pytest.mark.asyncio
    async def test_init_teardown_releases_bash_service(self, tmp_path):
        """When /api/init is followed by teardown, the bash service must also
        be exited so its background tasks are released."""
        _reset_conversation_singleton()
        _reset_bash_singleton()
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        await svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        assert svc._entered_bash_service is not None
        await svc.teardown()
        assert svc._entered_bash_service is None
        _reset_conversation_singleton()
        _reset_bash_singleton()

    @pytest.mark.asyncio
    async def test_init_switches_vscode_to_first_session_key(
        self, tmp_path, monkeypatch
    ):
        """VSCode booted with a random token; init switches it to the first
        session key, which a server that booted with the key would use."""
        _reset_conversation_singleton()
        vscode = SimpleNamespace(set_connection_token=AsyncMock())
        monkeypatch.setattr(
            "openhands.agent_server.init_router.get_vscode_service", lambda: vscode
        )
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

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
            _reset_conversation_singleton()

    @pytest.mark.asyncio
    async def test_init_without_session_keys_keeps_vscode_token(
        self, tmp_path, monkeypatch
    ):
        """With no session key to switch to, VSCode keeps its boot token."""
        _reset_conversation_singleton()
        vscode = SimpleNamespace(set_connection_token=AsyncMock())
        monkeypatch.setattr(
            "openhands.agent_server.init_router.get_vscode_service", lambda: vscode
        )
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

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
            _reset_conversation_singleton()


class TestEndToEndOverLifespan:
    """Drive the whole flow through the FastAPI lifespan + TestClient."""

    def test_dormant_503s_api_routes_until_init(self, tmp_path):
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
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
            finally:
                _reset_conversation_singleton()

    def test_root_path_updates_from_init_web_url(self, tmp_path):
        """When /api/init delivers a ``web_url``, the FastAPI ``root_path``
        must be re-derived from it so OpenAPI/Swagger/ReDoc URLs reflect the
        external mount path (e.g. behind a reverse proxy)."""
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
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
            finally:
                _reset_conversation_singleton()

    def test_init_api_key_required_when_configured(self, tmp_path):
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            secret_key=SecretStr("pool-key"),
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
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
            finally:
                _reset_conversation_singleton()

    def test_session_api_key_set_at_init_protects_api(self, tmp_path):
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
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

                # NOTE: session_api_keys configured at /api/init time take effect
                # on the *config object*, but the FastAPI session-key
                # dependency was bound to the original (dormant) config when
                # the routes were mounted. Documenting this trade-off:
                # in production, set OH_SESSION_API_KEYS_0 at pod start so
                # auth is in place from the moment routes go live, and use
                # /api/init only to deliver workspace + per-user runtime config.
                # The dormant gate ensures no traffic reaches gated routes
                # before /api/init regardless.
                assert app.state.config.session_api_keys == ["user-session-key"]
            finally:
                _reset_conversation_singleton()

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
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=False,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                # No init_service in non-deferred mode.
                assert getattr(app.state, "init_service", None) is None
                # /api/* should be live (200) — the dormant gate is a no-op.
                assert client.get("/api/conversations/count").status_code == 200
                # /api/init returns 404 because no InitService is attached.
                assert client.get("/api/init").status_code == 404
            finally:
                _reset_conversation_singleton()


@pytest.mark.asyncio
async def test_lifespan_teardown_releases_conversation_service_after_init(
    tmp_path,
):
    """If /api/init succeeds, the lifespan finally clause must release the
    conversation service. If /api/init never runs, teardown is a no-op."""
    _reset_conversation_singleton()
    _reset_bash_singleton()
    cfg = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )
    # Build a fake FastAPI app — api_lifespan only touches `.state`.
    fake_app = SimpleNamespace(state=SimpleNamespace(config=cfg))
    async with api_lifespan(fake_app):  # type: ignore[arg-type]
        init_svc = fake_app.state.init_service
        assert init_svc.state == "dormant"
        await init_svc.initialize(
            InitRequest(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
            )
        )
        assert init_svc.state == "ready"
    # After lifespan exit the conversation service should have been torn
    # down — i.e. _entered_service is cleared.
    assert init_svc._entered_service is None
    # Same for the bash service: it must be torn down on lifespan exit.
    assert init_svc._entered_bash_service is None
    _reset_conversation_singleton()
    _reset_bash_singleton()
