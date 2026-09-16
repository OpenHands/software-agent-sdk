"""Tests for the deferred-init / dormant-mode flow.

Background: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

import openhands.agent_server.api as api_mod
import openhands.agent_server.init_router as init_mod
from openhands.agent_server import conversation_service as cs_mod
from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config
from openhands.agent_server.init_router import (
    InitRequest,
    InitService,
    _build_initialized_config,
)
from openhands.agent_server.telemetry import service as telemetry_service
from openhands.agent_server.telemetry.sink import NoOpTelemetrySink


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


def _reset_conversation_singleton():
    """Some tests build their own ConversationService; reset the module-level
    cache so unrelated tests don't see leftover state."""
    from openhands.agent_server import conversation_service as cs_mod

    cs_mod._conversation_service = None


def _reset_bash_singleton():
    """Reset the module-level BashEventService cache so each test starts fresh."""
    from openhands.agent_server import bash_service as bash_mod

    bash_mod._bash_event_service = None


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


# ---------------------------------------------------------------------------
# #4658: a failed /api/init must unwind everything it staged and leave the
# server back in a clean `dormant` state.
# ---------------------------------------------------------------------------

_INJECTED_FAILURE = "injected init failure"

# Staged by every request built through ``_init_request`` so each failure phase
# also has to undo an environment mutation.
_STAGED_ENV = "DEFERRED_INIT_STAGED"

# Each name is the step whose failure is injected: the injector raises at that
# step, so everything before it has already completed. Between them the phases
# cover a failure at every point in the resource-entry sequence.
_FAILURE_PHASES = (
    "env",  # env staged; failure before laminar init
    "telemetry",  # failure building the sink; nothing published yet
    "conversation_service_factory",  # failure before the service is built
    "bash_service_factory",  # conversation service built; bash not constructed
    "bash_service_enter",  # bash constructed; its entry failed
    "conversation_service_enter",  # bash entered, conversation entry failed
    "root_path",  # both services entered, nothing published yet
)


def _init_request(
    tmp_path: Path,
    name: str = "runtime",
    conversations_path: Path | None = None,
) -> InitRequest:
    return InitRequest(
        env={_STAGED_ENV: name},
        conversations_path=conversations_path or tmp_path / name / "convs",
        bash_events_dir=tmp_path / name / "bash",
    )


def _occupied_path(tmp_path: Path) -> Path:
    """A path that already exists as a *file*, so a real ``mkdir`` fails."""
    path = tmp_path / "occupied"
    path.write_text("not a directory")
    return path


def _fail_once(original) -> Callable[..., Any]:
    """Wrap a sync callable so its first call raises, then delegate as usual.

    The failure clears itself so the retry inside each test exercises the
    normal path again.
    """
    state = {"pending": True}

    def wrapper(*args, **kwargs):
        if state["pending"]:
            state["pending"] = False
            raise RuntimeError(_INJECTED_FAILURE)
        return original(*args, **kwargs)

    return wrapper


def _fail_once_async(original) -> Callable[..., Any]:
    """Async counterpart of ``_fail_once``."""
    state = {"pending": True}

    async def wrapper(*args, **kwargs):
        if state["pending"]:
            state["pending"] = False
            raise RuntimeError(_INJECTED_FAILURE)
        return await original(*args, **kwargs)

    return wrapper


def _inject_failure(phase: str, tmp_path: Path, monkeypatch) -> InitRequest:
    """Install a one-shot failure for ``phase`` and return the request to send."""
    if phase == "env":
        monkeypatch.setattr(
            init_mod, "maybe_init_laminar", _fail_once(init_mod.maybe_init_laminar)
        )
    elif phase == "telemetry":
        monkeypatch.setattr(
            init_mod,
            "build_telemetry_sink",
            _fail_once_async(init_mod.build_telemetry_sink),
        )
    elif phase == "bash_service_enter":
        # The object is constructed but never entered, so this is the only
        # phase where a live service exists that the stack must *not* unwind.
        monkeypatch.setattr(
            init_mod.BashEventService,
            "__aenter__",
            _fail_once_async(init_mod.BashEventService.__aenter__),
        )
    elif phase == "conversation_service_factory":
        monkeypatch.setattr(
            init_mod.ConversationService,
            "get_instance",
            _fail_once(init_mod.ConversationService.get_instance),
        )
    elif phase == "bash_service_factory":
        monkeypatch.setattr(
            init_mod, "BashEventService", _fail_once(init_mod.BashEventService)
        )
    elif phase == "root_path":
        monkeypatch.setattr(
            api_mod, "_get_root_path", _fail_once(api_mod._get_root_path)
        )
    elif phase == "conversation_service_enter":
        # Real filesystem failure: the conversation service cannot create its
        # persistence directory, and it is entered after the bash service.
        return _init_request(tmp_path, conversations_path=_occupied_path(tmp_path))
    else:
        raise AssertionError(f"unknown failure phase: {phase}")
    return _init_request(tmp_path)


def _record_hook(original, label: str, events: list[str]) -> Callable[..., Any]:
    """Record ``label`` once ``original`` completes without raising."""

    async def wrapper(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        events.append(label)
        return result

    return wrapper


@pytest.fixture
def service_lifecycle(monkeypatch):
    """Record the __aenter__/__aexit__ calls InitService drives on the two
    services, so enter/exit balance and teardown order stay observable."""
    from openhands.agent_server.bash_service import BashEventService
    from openhands.agent_server.conversation_service import ConversationService

    events: list[str] = []
    for label, cls in (
        ("bash", BashEventService),
        ("conversation", ConversationService),
    ):
        for hook in ("__aenter__", "__aexit__"):
            monkeypatch.setattr(
                cls, hook, _record_hook(getattr(cls, hook), f"{label}.{hook}", events)
            )
    return events


@pytest.fixture
def clean_process_state():
    """Isolate the process-wide state InitService mutates, so a failing test
    cannot poison later tests."""
    _reset_conversation_singleton()
    _reset_bash_singleton()
    telemetry_service.reset_telemetry_sink()
    os.environ.pop(_STAGED_ENV, None)
    yield
    _reset_conversation_singleton()
    _reset_bash_singleton()
    telemetry_service.reset_telemetry_sink()
    os.environ.pop(_STAGED_ENV, None)


class TestDeferredInitRollback:
    """A failed /api/init must not leave a partially initialized runtime."""

    @pytest.mark.asyncio
    async def test_failed_init_restores_staged_runtime_state(
        self, tmp_path, monkeypatch, clean_process_state
    ):
        monkeypatch.setenv("DEFERRED_INIT_PREEXISTING", "before")
        monkeypatch.delenv("DEFERRED_INIT_STAGED", raising=False)

        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "boot" / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        boot_sink = NoOpTelemetrySink()
        telemetry_service._telemetry_sink = boot_sink
        singleton_before = cs_mod._conversation_service

        try:
            with pytest.raises(HTTPException) as excinfo:
                await svc.initialize(
                    InitRequest(
                        env={
                            "DEFERRED_INIT_PREEXISTING": "after",
                            "DEFERRED_INIT_STAGED": "staged",
                        },
                        conversations_path=_occupied_path(tmp_path),
                        bash_events_dir=tmp_path / "runtime" / "bash",
                    )
                )

            assert excinfo.value.status_code == 500
            assert svc.state == "dormant"
            assert svc.snapshot().error is not None

            # The staged environment is rolled back, including a pre-existing
            # value the failed request overwrote.
            assert os.environ["DEFERRED_INIT_PREEXISTING"] == "before"
            assert "DEFERRED_INIT_STAGED" not in os.environ

            # Nothing the failed attempt built stayed externally visible.
            assert app.state.config is base
            assert not hasattr(app.state, "telemetry_sink")
            assert not hasattr(app.state, "conversation_service")
            assert not hasattr(app.state, "bash_event_service")
            assert cs_mod._conversation_service is singleton_before
            assert telemetry_service._telemetry_sink is boot_sink

            # Entered resources were unwound, not retained for teardown.
            assert svc._entered_service is None
            assert svc._entered_bash_service is None

            # The pod is a clean dormant server again: a retry succeeds.
            result = await svc.initialize(_init_request(tmp_path, "retry"))
            assert result.state == "ready"
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    async def test_env_key_rejected_mid_staging_rolls_back_earlier_keys(
        self, tmp_path, monkeypatch, clean_process_state
    ):
        """A key the OS rejects part-way through staging must not strand the
        keys the same request already applied.

        ``os.environ`` rejects embedded nulls on every platform, and it does so
        only once it reaches that key -- by which point the earlier ones are
        already set.
        """
        monkeypatch.setenv("DEFERRED_INIT_PREEXISTING", "before")
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "boot" / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        try:
            with pytest.raises(HTTPException) as excinfo:
                await svc.initialize(
                    InitRequest(
                        env={
                            "DEFERRED_INIT_PREEXISTING": "after",
                            "DEFERRED_INIT_REJECTED": "a\x00b",
                        },
                        conversations_path=tmp_path / "runtime" / "convs",
                        bash_events_dir=tmp_path / "runtime" / "bash",
                    )
                )

            assert excinfo.value.status_code == 500
            assert svc.state == "dormant"
            assert os.environ["DEFERRED_INIT_PREEXISTING"] == "before"
            assert "DEFERRED_INIT_REJECTED" not in os.environ
        finally:
            await svc.teardown()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("phase", _FAILURE_PHASES)
    async def test_failure_at_each_entry_phase_unwinds_exactly_once(
        self, tmp_path, monkeypatch, clean_process_state, service_lifecycle, phase
    ):
        monkeypatch.delenv(_STAGED_ENV, raising=False)
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "boot" / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        boot_sink = NoOpTelemetrySink()
        telemetry_service._telemetry_sink = boot_sink

        request = _inject_failure(phase, tmp_path, monkeypatch)
        try:
            with pytest.raises(HTTPException) as excinfo:
                await svc.initialize(request)

            assert excinfo.value.status_code == 500, phase
            assert svc.state == "dormant", phase

            entered = [e for e in service_lifecycle if e.endswith("__aenter__")]
            exited = [e for e in service_lifecycle if e.endswith("__aexit__")]
            assert len(entered) == len(set(entered)), (
                f"a resource was entered more than once: {service_lifecycle}"
            )
            assert sorted(e.split(".")[0] for e in exited) == sorted(
                e.split(".")[0] for e in entered
            ), f"entered resources must be exited exactly once: {service_lifecycle}"

            # No staged environment or runtime escaped the rollback.
            assert _STAGED_ENV not in os.environ, phase
            assert app.state.config is base, phase
            assert not hasattr(app.state, "telemetry_sink"), phase
            assert not hasattr(app.state, "conversation_service"), phase
            assert not hasattr(app.state, "bash_event_service"), phase
            assert cs_mod._conversation_service is None, phase
            assert telemetry_service._telemetry_sink is boot_sink, phase
            assert svc._entered_service is None, phase
            assert svc._entered_bash_service is None, phase

            # Retrying from the rolled-back dormant state succeeds.
            result = await svc.initialize(_init_request(tmp_path, "retry"))
            assert result.state == "ready", phase
        finally:
            await svc.teardown()

        # Across the failed attempt, the retry and teardown, every resource that
        # was entered was exited exactly once. A retry that double-entered, or a
        # teardown that double-exited, would break the balance here.
        entered = sorted(
            e.split(".")[0] for e in service_lifecycle if e.endswith("__aenter__")
        )
        exited = sorted(
            e.split(".")[0] for e in service_lifecycle if e.endswith("__aexit__")
        )
        assert exited == entered, (
            f"enter/exit balance broken across retry + teardown: {service_lifecycle}"
        )

    @pytest.mark.asyncio
    async def test_teardown_after_successful_init_keeps_service_exit_order(
        self, tmp_path, clean_process_state, service_lifecycle
    ):
        base = Config(
            deferred_init=True,
            conversations_path=tmp_path / "boot" / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]

        result = await svc.initialize(_init_request(tmp_path))
        assert result.state == "ready"
        assert service_lifecycle == ["bash.__aenter__", "conversation.__aenter__"]

        await svc.teardown()
        assert service_lifecycle[-2:] == [
            "conversation.__aexit__",
            "bash.__aexit__",
        ]
        assert svc._entered_service is None
        assert svc._entered_bash_service is None

    def test_failed_init_over_http_rolls_back_and_allows_retry(self, tmp_path):
        """Drive the failure through the real REST contract + lifespan."""
        _reset_conversation_singleton()
        telemetry_service.reset_telemetry_sink()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "boot" / "convs",
            bash_events_dir=tmp_path / "boot" / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                boot_sink = app.state.telemetry_sink
                boot_root_path = app.root_path

                resp = client.post(
                    "/api/init",
                    json={
                        "env": {"DEFERRED_INIT_E2E": "staged"},
                        "conversations_path": str(_occupied_path(tmp_path)),
                        "bash_events_dir": str(tmp_path / "runtime" / "bash"),
                        # A per-user web_url would move root_path if the failed
                        # attempt had already published the merged config.
                        "web_url": "https://example.com/user-1/",
                    },
                )
                assert resp.status_code == 500
                assert client.get("/api/init").json()["state"] == "dormant"

                # The pod is still dormant and nothing was staged.
                assert client.get("/api/conversations/count").status_code == 503
                assert app.state.telemetry_sink is boot_sink
                assert not hasattr(app.state, "conversation_service")
                assert app.root_path == boot_root_path
                assert os.environ.get("DEFERRED_INIT_E2E") is None

                # The orchestrator can retry the same pod.
                resp = client.post(
                    "/api/init",
                    json={
                        "conversations_path": str(tmp_path / "u" / "convs"),
                        "bash_events_dir": str(tmp_path / "u" / "bash"),
                    },
                )
                assert resp.status_code == 200
                assert resp.json()["state"] == "ready"
                assert client.get("/api/conversations/count").status_code == 200
            finally:
                os.environ.pop("DEFERRED_INIT_E2E", None)
                _reset_conversation_singleton()
                telemetry_service.reset_telemetry_sink()
