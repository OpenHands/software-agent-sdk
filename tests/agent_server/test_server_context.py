"""One ``ServerContext`` per (re)initialisation, installed in one piece.

A server derives its stores, registry and services from one config and keeps
them together in a ``ServerContext``. ``POST /api/init`` builds the context of
the delivered config beside the dormant one and swaps it in with a single
assignment, so no request can see the new config with the old stores, or the
old key with the new services; a failed init leaves the dormant context as it
was. Nothing in the server reads a process-wide default instead (see the guard
at the end of this file).
"""

from __future__ import annotations

import ast
import dataclasses
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

import openhands.agent_server
from openhands.agent_server import init_router as init_router_module
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.server_context import ServerContext, server_context_of
from openhands.sdk.utils.cipher import Cipher


BOOT_KEY = "boot-secret-key"
USER_KEY = "user-secret-key"
SESSION_KEY = "user-session-key"


@pytest.fixture(autouse=True)
def _fresh_vscode_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/init touches the VSCode service singleton; give each test its own."""
    monkeypatch.setattr("openhands.agent_server.vscode_service._vscode_service", None)


@pytest.fixture
def dormant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A deferred-init server that booted with the pool's key and directory."""
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "boot" / "persistence"))
    config = Config(
        deferred_init=True,
        secret_key=SecretStr(BOOT_KEY),
        conversations_path=tmp_path / "boot" / "conversations",
        bash_events_dir=tmp_path / "boot" / "bash",
        enable_vscode=False,
        preload_tools=False,
    )
    with TestClient(create_app(config)) as client:
        yield client


def _app(client: TestClient) -> FastAPI:
    app = client.app
    assert isinstance(app, FastAPI)
    return app


def _init(client: TestClient, tmp_path: Path):
    """Deliver the user's key, session key and directories through /api/init."""
    return client.post(
        "/api/init",
        headers={"X-Init-API-Key": BOOT_KEY},
        json={
            "secret_key": USER_KEY,
            "session_api_keys": [SESSION_KEY],
            "conversations_path": str(tmp_path / "user" / "conversations"),
            "bash_events_dir": str(tmp_path / "user" / "bash"),
            "env": {"OH_PERSISTENCE_DIR": str(tmp_path / "user" / "persistence")},
        },
    )


def test_init_swaps_config_stores_and_services_together(
    dormant: TestClient, tmp_path: Path
) -> None:
    app = _app(dormant)
    before = server_context_of(app)
    assert before.conversation_service is None
    assert before.bash_event_service is None

    assert _init(dormant, tmp_path).status_code == 200

    after = server_context_of(app)
    assert after is not before
    # Everything in the new context was built from the delivered config ...
    assert after.config.deferred_init is False
    assert after.config.session_api_keys == [SESSION_KEY]
    assert after.stores is not before.stores
    assert after.stores.cipher is after.config.cipher
    assert after.stores.profile_dir == tmp_path / "user" / "persistence"
    assert after.conversation_registry is not before.conversation_registry
    assert after.conversation_registry.config is after.config
    assert after.conversation_registry.stores is after.stores
    assert after.conversation_service is not None
    assert after.conversation_service.stores is after.stores
    assert after.conversation_service.cipher is after.config.cipher
    assert after.bash_event_service is not None
    assert after.bash_event_service.bash_events_dir == tmp_path / "user" / "bash"
    # ... and the dormant one was replaced, not edited.
    assert before.config.deferred_init is True
    assert before.conversation_service is None
    assert before.stores.profile_dir == tmp_path / "boot" / "persistence"


def test_requests_after_init_use_the_delivered_key_and_directory(
    dormant: TestClient, tmp_path: Path
) -> None:
    """No reset step: the next request already runs on the new stores."""
    assert _init(dormant, tmp_path).status_code == 200

    saved = dormant.put(
        "/api/settings/secrets",
        headers={"X-Session-API-Key": SESSION_KEY},
        json={"name": "API_TOKEN", "value": "token-value"},
    )
    assert saved.status_code == 200

    secrets_file = tmp_path / "user" / "persistence" / "secrets.json"
    stored = json.loads(secrets_file.read_text())["custom_secrets"]["API_TOKEN"]
    assert Cipher(USER_KEY).try_decrypt_str(stored["secret"]) == "token-value"
    assert Cipher(BOOT_KEY).try_decrypt_str(stored["secret"]) is None
    assert not (tmp_path / "boot" / "persistence" / "secrets.json").exists()


def test_a_failed_init_leaves_the_dormant_context_in_place(
    dormant: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(dormant)
    before = server_context_of(app)
    started: list[ConversationService] = []
    real_get_instance = ConversationService.get_instance

    def recording_get_instance(config, stores):
        service = real_get_instance(config, stores)
        started.append(service)
        return service

    class _FailingVSCode:
        async def set_connection_token(self, _token: str) -> None:
            raise RuntimeError("vscode refused the token")

    monkeypatch.setattr(
        init_router_module.ConversationService, "get_instance", recording_get_instance
    )
    # Fails after the new stores, registry and services are all up.
    monkeypatch.setattr(
        init_router_module, "get_vscode_service", lambda _config: _FailingVSCode()
    )

    failed = _init(dormant, tmp_path)

    assert failed.status_code == 500
    assert dormant.get("/api/init").json()["state"] == "dormant"
    # The very same context object, with nothing of the attempt in it ...
    assert server_context_of(app) is before
    assert before.conversation_service is None
    assert before.config.deferred_init is True
    # ... and the service the attempt started was stopped, not left running.
    (attempted,) = started
    assert attempted._event_services is None
    # The dormant key still guards /api/init, so the orchestrator can retry.
    assert dormant.post("/api/init", json={}).status_code == 401

    monkeypatch.setattr(init_router_module, "get_vscode_service", lambda _config: None)
    assert _init(dormant, tmp_path).status_code == 200
    retried = server_context_of(app)
    assert retried is not before
    assert retried.conversation_service is started[-1]


def test_the_lifespan_adds_services_to_the_context_it_was_created_with(
    tmp_path: Path,
) -> None:
    """A normal boot keeps its config, stores and registry; only services come."""
    config = Config(
        session_api_keys=[],
        conversations_path=tmp_path / "conversations",
        bash_events_dir=tmp_path / "bash",
        enable_vscode=False,
        preload_tools=False,
    )
    app = create_app(config)
    created = server_context_of(app)
    assert created.conversation_service is None

    with TestClient(app):
        running = server_context_of(app)
        assert running.config is created.config
        assert running.stores is created.stores
        assert running.conversation_registry is created.conversation_registry
        assert running.conversation_service is not None
        assert running.conversation_service.stores is created.stores
        assert running.bash_event_service is not None
        # Built from the config given to create_app, not from the environment.
        assert running.bash_event_service.bash_events_dir == tmp_path / "bash"


def test_an_app_without_a_context_says_so() -> None:
    with pytest.raises(RuntimeError, match="no ServerContext"):
        server_context_of(FastAPI())


# ── Guard: the context is the only way in ───────────────────────────────────

# What a server derives from its config. Only ``server_context.py`` may keep
# any of it on ``app.state``; everything else asks the context.
_CONTEXT_STATE = frozenset(
    {
        "server_context",
        "config",
        "conversation_service",
        "bash_event_service",
        "conversation_registry",
    }
)
_CONTEXT_MODULE = "server_context.py"
_PACKAGE_DIR = Path(openhands.agent_server.__file__).parent


def _is_app_state(node: ast.expr) -> bool:
    """``<anything>.state`` -- an app's (or a stand-in's) state bag."""
    return isinstance(node, ast.Attribute) and node.attr == "state"


def context_violations(source: str, filename: str) -> list[str]:
    """Ways ``source`` reaches server context state without going through it."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("get_default_"):
                found.append(f"{filename}:{node.lineno}: defines {node.name}()")
        elif isinstance(node, ast.Attribute):
            if node.attr in _CONTEXT_STATE and _is_app_state(node.value):
                found.append(f"{filename}:{node.lineno}: reads .state.{node.attr}")
        elif isinstance(node, ast.Call):
            # getattr/setattr/hasattr(<x>.state, "config", ...)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
                and len(node.args) >= 2
                and _is_app_state(node.args[0])
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in _CONTEXT_STATE
            ):
                found.append(
                    f"{filename}:{node.lineno}: {node.func.id}(.state, "
                    f"{node.args[1].value!r})"
                )
    return found


@pytest.mark.parametrize(
    "source",
    [
        "config = request.app.state.config",
        "service = websocket.app.state.conversation_service",
        "app.state.bash_event_service = service",
        "registry = scope['app'].state.conversation_registry",
        "context = api.state.server_context",
        "config = getattr(request.app.state, 'config', None)",
        "def get_default_config():\n    return load_config()",
        "async def get_default_conversation_service():\n    ...",
    ],
)
def test_the_guard_recognises_a_way_around_the_context(source: str) -> None:
    assert context_violations(source, "example.py")


@pytest.mark.parametrize(
    "source",
    [
        "config = get_config(request)",
        "stores = get_server_context(request).stores",
        "manager = request.app.state.canvas_extension_backend_manager",
        "event_service = getattr(request.state, 'runtime_event_service', None)",
        "status = conversation.state.execution_status",
        "def _get_default_parsers():\n    return {}",
    ],
)
def test_the_guard_accepts_the_context_and_unrelated_state(source: str) -> None:
    assert context_violations(source, "example.py") == []


def test_only_the_server_context_module_touches_context_state() -> None:
    """No module reads config-derived ``app.state`` or defines ``get_default_*``.

    A reader of ``app.state.config`` (or of a service kept there) sees whatever
    was assigned last, which during ``POST /api/init`` can be half of the old
    setup and half of the new one; a ``get_default_*`` function builds a second
    copy from the environment. Take the config, a store or a service from the
    request's ``ServerContext`` (``openhands.agent_server.dependencies``), or
    have it passed in by the code that built the context.
    """
    violations: list[str] = []
    modules = sorted(_PACKAGE_DIR.rglob("*.py"))
    assert len(modules) > 50, "the agent-server package was not found"
    for path in modules:
        relative = path.relative_to(_PACKAGE_DIR).as_posix()
        found = context_violations(path.read_text(), relative)
        if relative == _CONTEXT_MODULE:
            # The one place the context itself is kept and read.
            found = [v for v in found if not v.endswith(".state.server_context")]
        violations.extend(found)

    assert violations == [], "\n".join(violations)


def test_the_context_module_keeps_the_context_on_app_state() -> None:
    """The guard's exception is real: that module is where the context lives."""
    source = (_PACKAGE_DIR / _CONTEXT_MODULE).read_text()
    kept = context_violations(source, _CONTEXT_MODULE)
    assert kept and all(v.endswith(".state.server_context") for v in kept)


def test_a_context_cannot_be_edited_in_place(tmp_path: Path) -> None:
    """Changing a server means installing another context, never mutating one."""
    context = server_context_of(
        create_app(Config(conversations_path=tmp_path / "conversations"))
    )
    assert isinstance(context, ServerContext)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(context, "config", Config())
