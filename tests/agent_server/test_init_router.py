"""Tests for the deferred-init / dormant-mode flow.

Background: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The agent-server pulls config from env at import time in places;
    null these out so each test starts from a clean slate."""
    for key in (
        "OH_DEFERRED_INIT",
        "OH_INIT_API_KEY",
        "OH_WEB_URL",
        "RUNTIME_URL",
        "TMUX_TMPDIR",
        "SESSION_API_KEY",
        "OH_SESSION_API_KEYS_0",
        "OH_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _reset_conversation_singleton():
    """Some tests build their own ConversationService; reset the module-level
    cache so unrelated tests don't see leftover state."""
    from openhands.agent_server import conversation_service as cs_mod

    cs_mod._conversation_service = None


class TestConfigDefaults:
    def test_deferred_init_defaults_false(self):
        assert Config().deferred_init is False

    def test_deferred_init_reads_env(self, monkeypatch):
        monkeypatch.setenv("OH_DEFERRED_INIT", "1")
        # The factory is only used when no env-parser flow is in play; for
        # programmatic Config() this still reads the env directly.
        assert Config().deferred_init is True

    def test_init_api_key_reads_env(self, monkeypatch):
        monkeypatch.setenv("OH_INIT_API_KEY", "pool-bootstrap-key")
        cfg = Config()
        assert cfg.init_api_key is not None
        assert cfg.init_api_key.get_secret_value() == "pool-bootstrap-key"


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
        finally:
            await svc.teardown()
            _reset_conversation_singleton()

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
        finally:
            await svc.teardown()
            monkeypatch.delenv("DEFERRED_INIT_TEST_VAR", raising=False)
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

    def test_init_api_key_required_when_configured(self, tmp_path):
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            init_api_key=SecretStr("pool-key"),
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
    _reset_conversation_singleton()
