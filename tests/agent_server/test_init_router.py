"""Tests for the deferred-init / dormant-mode flow.

Background: https://github.com/OpenHands/software-agent-sdk/issues/2523
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config
from openhands.agent_server.init_router import (
    InitRequest,
    InitService,
    _build_initialized_config,
)
from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX


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
        "OH_INIT_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


# A dormant server has no cipher key of its own, so every /api/init must carry
# one. Tests that aren't about the key itself use this to supply a throwaway.
CIPHER_KEY = "test-cipher-key"
# The bootstrap credential the pool gives the pod at birth. Distinct from
# CIPHER_KEY throughout: keeping the two apart is the point of the split.
BOOT_KEY = "test-boot-key"


def _init_request(**kwargs) -> InitRequest:
    """An InitRequest with the now-mandatory ``secret_key`` filled in."""
    kwargs.setdefault("secret_key", SecretStr(CIPHER_KEY))
    return InitRequest(**kwargs)


def _dormant_config(**kwargs) -> Config:
    """A dormant Config carrying an init credential, the way a warm pod does."""
    kwargs.setdefault("deferred_init", True)
    kwargs.setdefault("init_api_key", SecretStr(BOOT_KEY))
    return Config(**kwargs)


def _post_init(client: TestClient, **body):
    """POST /api/init authenticated, with the mandatory cipher key filled in."""
    body.setdefault("secret_key", CIPHER_KEY)
    return client.post("/api/init", headers={"X-Init-API-Key": BOOT_KEY}, json=body)


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


class TestDeferredInitKeepsTheCipherKeyOutOfTheEnvironment:
    """``deferred_init=True`` refuses to take a cipher key from the environment.

    The agent that a dormant pod goes on to run can read its own pod
    environment, so a cipher key resolved from there is one the agent can use to
    decrypt the user's own secrets. The key arrives in the /api/init payload
    instead. See spec/FUSEY_CIPHER_KEY_PLAN.md in the runtime-api repo.
    """

    def test_explicit_secret_key_is_discarded(self):
        cfg = Config(deferred_init=True, secret_key=SecretStr("from-the-env"))
        assert cfg.secret_key is None
        assert cfg.cipher is None

    @pytest.mark.parametrize(
        "env_var", ["SESSION_API_KEY", "OH_SESSION_API_KEYS_0", "OH_SECRET_KEY"]
    )
    def test_no_env_var_supplies_a_cipher_key(self, env_var, monkeypatch):
        monkeypatch.setenv(env_var, "env-key")
        assert Config(deferred_init=True).secret_key is None

    def test_non_deferred_config_keeps_env_resolution(self, monkeypatch):
        monkeypatch.setenv("SESSION_API_KEY", "env-key")
        cfg = Config()
        assert cfg.secret_key is not None
        assert cfg.secret_key.get_secret_value() == "env-key"


class TestInitApiKey:
    """The bootstrap credential, which *may* live in the environment."""

    def test_defaults_to_none_without_env(self):
        assert Config().init_api_key is None

    @pytest.mark.parametrize(
        "env_var",
        [
            "OH_INIT_API_KEY",
            "SESSION_API_KEY",
            "OH_SESSION_API_KEYS_0",
            "OH_SECRET_KEY",
        ],
    )
    def test_resolves_from_env(self, env_var, monkeypatch):
        monkeypatch.setenv(env_var, "boot-key")
        cfg = Config(deferred_init=True)
        assert cfg.init_api_key is not None
        assert cfg.init_api_key.get_secret_value() == "boot-key"

    def test_survives_deferred_mode(self):
        """Unlike secret_key: this one is meant to come from the environment."""
        cfg = Config(deferred_init=True, init_api_key=SecretStr("boot-key"))
        assert cfg.init_api_key is not None
        assert cfg.init_api_key.get_secret_value() == "boot-key"


class TestBuildInitializedConfig:
    def test_clears_deferred_init_flag(self):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(base, _init_request())
        assert merged.deferred_init is False

    def test_overrides_only_provided_fields(self, tmp_path):
        base = Config(
            deferred_init=True,
            conversations_path=Path("base/convs"),
            bash_events_dir=Path("base/bash"),
            max_concurrent_runs=5,
        )
        req = _init_request(
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

    def test_init_without_a_secret_key_is_rejected(self):
        """There is deliberately nothing left to fall back to.

        This used to adopt the caller's first session key. That is the same leak
        under another name — session keys are handed to the client, and a resume
        rotates them, after which the workspace no longer decrypts.
        """
        base = Config(deferred_init=True)
        assert base.secret_key is None
        with pytest.raises(HTTPException) as excinfo:
            _build_initialized_config(base, InitRequest(session_api_keys=["s1", "s2"]))
        assert excinfo.value.status_code == 400
        assert "secret_key is required" in str(excinfo.value.detail)

    def test_secret_key_comes_from_the_payload(self):
        base = Config(deferred_init=True)
        merged = _build_initialized_config(
            base,
            InitRequest(
                session_api_keys=["sk"], secret_key=SecretStr("explicit-secret")
            ),
        )
        assert merged.secret_key is not None
        assert merged.secret_key.get_secret_value() == "explicit-secret"

    def test_init_api_key_is_cleared(self):
        """One-shot: the bootstrap credential authorises init and nothing after."""
        base = Config(deferred_init=True, init_api_key=SecretStr("boot-key"))
        merged = _build_initialized_config(base, _init_request())
        assert merged.init_api_key is None

    def test_cipher_is_rebuilt_when_secret_key_changes(self):
        """A memoised cipher on the dormant config must not survive the merge.

        ``Config.cipher`` caches the built Cipher on the instance and
        ``model_copy`` copies it along with the fields. Without an explicit
        eviction the initialized server reports the new ``secret_key`` while
        every encrypt/decrypt still uses the boot key, which silently nulls
        the secrets of a conversation directory attached at /api/init time.

        A deferred config can no longer hold a boot key to be caught out by, so
        the base is built non-deferred and flipped with ``model_copy``, which
        skips validators. That keeps the eviction itself under test rather than
        resting on the nulling above to make it unreachable.
        """
        base = Config(
            session_api_keys=["pod-boot"],
            secret_key=SecretStr("pod-boot"),
        ).model_copy(update={"deferred_init": True})
        boot_cipher = base.cipher
        assert boot_cipher is not None and boot_cipher.secret_key == "pod-boot"

        merged = _build_initialized_config(
            base,
            InitRequest(
                session_api_keys=["user-session"],
                secret_key=SecretStr("conversation-key"),
            ),
        )
        assert merged.cipher is not None
        assert merged.cipher.secret_key == "conversation-key"
        # The dormant config keeps its own cipher — the merge is not in place.
        assert base.cipher is boot_cipher

    def test_cipher_is_built_after_a_pre_init_read_returned_none(self):
        """The live path: a dormant config has no cipher, and reads memoise that.

        ``Config.cipher`` caches ``None`` just as eagerly as it caches a real
        Cipher, so a pre-init read must not be able to leave the initialized
        server unable to encrypt.
        """
        base = Config(deferred_init=True)
        assert base.cipher is None
        merged = _build_initialized_config(
            base, InitRequest(secret_key=SecretStr("conversation-key"))
        )
        assert merged.cipher is not None
        assert merged.cipher.secret_key == "conversation-key"


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
            _init_request(
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
            _init_request(
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
            _init_request(
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
            _init_request(
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
            _init_request(
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
        cfg = _dormant_config(
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
                resp = _post_init(
                    client,
                    conversations_path=str(tmp_path / "u" / "convs"),
                    bash_events_dir=str(tmp_path / "u" / "bash"),
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
        cfg = _dormant_config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                # Dormant server has no web_url → empty root_path.
                assert app.root_path == ""

                resp = _post_init(
                    client,
                    web_url="https://example.com/agent-server-123/agent-server/",
                    conversations_path=str(tmp_path / "u" / "convs"),
                    bash_events_dir=str(tmp_path / "u" / "bash"),
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
        cfg = _dormant_config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                body = {
                    "secret_key": CIPHER_KEY,
                    "conversations_path": str(tmp_path / "u" / "convs"),
                    "bash_events_dir": str(tmp_path / "u" / "bash"),
                }

                # Wrong key → 401.
                resp = client.post(
                    "/api/init", headers={"X-Init-API-Key": "wrong"}, json=body
                )
                assert resp.status_code == 401

                # No key → 401.
                resp = client.post("/api/init", json=body)
                assert resp.status_code == 401

                # The cipher key is not the init credential: presenting it
                # where the boot key belongs must not authenticate.
                resp = client.post(
                    "/api/init", headers={"X-Init-API-Key": CIPHER_KEY}, json=body
                )
                assert resp.status_code == 401

                # Right key → 200.
                resp = client.post(
                    "/api/init", headers={"X-Init-API-Key": BOOT_KEY}, json=body
                )
                assert resp.status_code == 200

                # GET /api/init does NOT require the key (status polling).
                resp = client.get("/api/init")
                assert resp.status_code == 200
            finally:
                _reset_conversation_singleton()

    def test_init_fails_closed_without_a_configured_credential(self, tmp_path):
        """An open init endpoint on a reachable dormant pod would let anyone
        install their own session keys, cipher key and webhooks."""
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        assert cfg.init_api_key is None
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                resp = client.post("/api/init", json={"secret_key": CIPHER_KEY})
                assert resp.status_code == 401
                assert "OH_INIT_API_KEY" in resp.json()["detail"]
                # Still dormant: nothing was initialised.
                assert client.get("/api/init").json()["state"] == "dormant"
            finally:
                _reset_conversation_singleton()

    def test_allow_unauthenticated_init_opens_the_gate_for_dev(self, tmp_path):
        _reset_conversation_singleton()
        cfg = Config(
            deferred_init=True,
            allow_unauthenticated_init=True,
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                resp = client.post(
                    "/api/init",
                    json={
                        "secret_key": CIPHER_KEY,
                        "conversations_path": str(tmp_path / "u" / "convs"),
                        "bash_events_dir": str(tmp_path / "u" / "bash"),
                    },
                )
                assert resp.status_code == 200
            finally:
                _reset_conversation_singleton()

    def test_init_without_a_secret_key_is_a_400_and_stays_dormant(self, tmp_path):
        _reset_conversation_singleton()
        cfg = _dormant_config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                resp = client.post(
                    "/api/init",
                    headers={"X-Init-API-Key": BOOT_KEY},
                    json={"session_api_keys": ["user-session-key"]},
                )
                assert resp.status_code == 400, resp.text
                assert "secret_key is required" in resp.json()["detail"]

                # Rolled back, so a corrected retry still works.
                status_resp = client.get("/api/init")
                assert status_resp.json()["state"] == "dormant"
                assert _post_init(client).status_code == 200
            finally:
                _reset_conversation_singleton()

    def test_session_api_key_set_at_init_protects_api(self, tmp_path):
        _reset_conversation_singleton()
        cfg = _dormant_config(
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
                resp = _post_init(
                    client,
                    session_api_keys=["user-session-key"],
                    conversations_path=str(tmp_path / "u" / "convs"),
                    bash_events_dir=str(tmp_path / "u" / "bash"),
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


class TestStoreSingletonsAreRebuiltAtInit:
    """The settings/secrets stores are process-wide singletons that capture
    their cipher and persistence directory from whoever builds them first.

    On a dormant pod that can happen before /api/init — telemetry with a
    configured exporter reads the settings store during startup — which would
    pin ``cipher=None`` and the pre-init OH_PERSISTENCE_DIR for the life of the
    process, silently dropping every secret written afterwards.
    """

    @pytest.mark.asyncio
    async def test_a_pre_init_store_does_not_keep_its_null_cipher(
        self, tmp_path, monkeypatch
    ):
        from openhands.agent_server.persistence.store import (
            get_secrets_store,
            get_settings_store,
            reset_stores,
        )

        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "boot"))
        reset_stores()
        _reset_conversation_singleton()
        _reset_bash_singleton()

        base = _dormant_config(
            conversations_path=tmp_path / "convs",
            bash_events_dir=tmp_path / "bash",
        )
        # Something reads the store while the pod is still dormant.
        assert get_settings_store(base).cipher is None

        app = SimpleNamespace(state=SimpleNamespace(config=base))
        svc = InitService(app, base_config=base)  # type: ignore[arg-type]
        await svc.initialize(
            _init_request(
                conversations_path=tmp_path / "u" / "convs",
                bash_events_dir=tmp_path / "u" / "bash",
                env={"OH_PERSISTENCE_DIR": str(tmp_path / "user")},
            )
        )
        try:
            settings_store = get_settings_store(app.state.config)
            assert settings_store.cipher is not None, (
                "the settings store kept the cipher-less instance built while "
                "the pod was dormant; secrets written after init would be "
                "persisted in the clear or dropped"
            )
            assert settings_store.cipher.secret_key == CIPHER_KEY
            # ...and it follows the persistence dir delivered at init, not the
            # one that was in the environment at boot.
            assert settings_store.persistence_dir == tmp_path / "user"

            secrets_store = get_secrets_store(app.state.config)
            assert secrets_store.cipher is not None
            assert secrets_store.cipher.secret_key == CIPHER_KEY
        finally:
            await svc.teardown()
            reset_stores()
            _reset_conversation_singleton()
            _reset_bash_singleton()


class TestSecretsOnAnAttachedConversationDirectory:
    """Warm-pool flow: a conversation directory written by an earlier server is
    attached to a dormant pod, and /api/init supplies the key it was encrypted
    with. See https://github.com/OpenHands/software-agent-sdk/issues/2523.
    """

    @pytest.mark.asyncio
    async def test_init_secret_key_decrypts_attached_conversation(self, tmp_path):
        from openhands.agent_server.conversation_service import ConversationService
        from openhands.sdk import LLM, Agent, LocalWorkspace
        from openhands.sdk.conversation.request import StartConversationRequest
        from openhands.sdk.secret.secrets import StaticSecret
        from openhands.sdk.security.confirmation_policy import NeverConfirm
        from openhands.sdk.utils.cipher import Cipher

        volume_key = "volume-cipher-key"
        convs = tmp_path / "volume" / "conversations"
        workspace = tmp_path / "volume" / "project"
        workspace.mkdir(parents=True)

        # An earlier server writes the volume under ``volume_key``.
        _reset_conversation_singleton()
        _reset_bash_singleton()
        async with ConversationService(
            conversations_dir=convs,
            cipher=Cipher(volume_key),
            lease_ttl_seconds=0.0,
        ) as writer:
            info, _ = await writer.start_conversation(
                StartConversationRequest(
                    agent=Agent(
                        llm=LLM(
                            model="gpt-4o",
                            usage_id="test-llm",
                            api_key=SecretStr("sk-persisted"),
                        ),
                        tools=[],
                    ),
                    workspace=LocalWorkspace(working_dir=str(workspace)),
                    confirmation_policy=NeverConfirm(),
                    secrets={
                        "DEPLOY_TOKEN": StaticSecret(value=SecretStr("dpl-persisted"))
                    },
                )
            )
            conversation_id = info.id
        _reset_conversation_singleton()
        _reset_bash_singleton()

        # A warm pod boots with a bootstrap credential and no cipher key.
        cfg = _dormant_config(
            session_api_keys=["pod-boot"],
            conversations_path=convs,
            bash_events_dir=tmp_path / "volume" / "bash",
            lease_ttl_seconds=0.0,
            enable_vscode=False,
        )
        app = create_app(cfg)
        with TestClient(app) as client:
            try:
                # Force the dormant config to memoise the absence of a cipher,
                # the way any import-time or pre-init read would.
                assert app.state.config.cipher is None

                resp = client.post(
                    "/api/init",
                    headers={"X-Init-API-Key": BOOT_KEY},
                    json={
                        "session_api_keys": ["user-session"],
                        "secret_key": volume_key,
                    },
                )
                assert resp.status_code == 200, resp.text

                service = app.state.conversation_service
                assert service.cipher is not None
                assert service.cipher.secret_key == volume_key

                stored = service._conversation_records[conversation_id].stored
                assert stored.agent.llm.api_key is not None, (
                    "llm.api_key decrypted to None: the attached conversation was "
                    "read with the pod's boot key instead of the key delivered "
                    "by /api/init"
                )
                assert stored.agent.llm.api_key.get_secret_value() == "sk-persisted"
                assert stored.secrets["DEPLOY_TOKEN"].get_value() == "dpl-persisted"
            finally:
                _reset_conversation_singleton()
                _reset_bash_singleton()

        # The volume must still hold ciphertext — a wrong-key read would have
        # rewritten these as null on the way out.
        meta = json.loads((convs / conversation_id.hex / "meta.json").read_text())
        assert str(meta["agent"]["llm"]["api_key"]).startswith(FERNET_TOKEN_PREFIX)
        assert str(meta["secrets"]["DEPLOY_TOKEN"]["value"]).startswith(
            FERNET_TOKEN_PREFIX
        )


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
            _init_request(
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
