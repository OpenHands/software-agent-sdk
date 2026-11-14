import os
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.config import (
    V0_SESSION_API_KEY_ENV,
    V1_SESSION_API_KEY_ENV,
    Config,
)
from openhands.agent_server.conversation_registry import ConversationRegistry
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.persistence import ServerStores
from openhands.agent_server.server_context import (
    ServerContext,
    install_server_context,
    server_context_of,
)
from openhands.sdk.mcp.config import MCPServer


class _FakeMCPClient:
    def __init__(self, tools: list[str]):
        self.tools = [SimpleNamespace(name=name) for name in tools]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class FakeMCPTools:
    """Stands in for ``create_mcp_tools``: records each call and returns a
    client listing ``tools``. ``on_create`` sees the config and the keyword
    arguments (OAuth storage and factory) before the client is returned."""

    def __init__(self) -> None:
        self.configs: list[dict[str, MCPServer]] = []
        self.calls: list[dict[str, Any]] = []
        self.tools: list[str] = []
        self.on_create: Callable[[dict[str, MCPServer], dict[str, Any]], None] | None
        self.on_create = None

    def __call__(
        self, config: dict[str, MCPServer], timeout: float = 30.0, **kwargs: Any
    ) -> _FakeMCPClient:
        self.configs.append(config)
        self.calls.append(kwargs)
        if self.on_create is not None:
            self.on_create(config, kwargs)
        return _FakeMCPClient(self.tools)


@pytest.fixture
def fake_mcp_tools(monkeypatch: pytest.MonkeyPatch) -> FakeMCPTools:
    """Replace the MCP router's ``create_mcp_tools`` with a recording fake."""
    fake = FakeMCPTools()
    monkeypatch.setattr("openhands.agent_server.mcp_router.create_mcp_tools", fake)
    return fake


@pytest.fixture(autouse=True)
def isolate_session_api_key_env():
    """Put the session-key variables back the way each test found them.

    The server exports its effective key to ``OH_SESSION_API_KEYS_0`` when it
    starts or is initialized (``export_session_api_key``). Tests that drive
    those paths directly, or call the helper, would otherwise leave the key in
    the process environment, and every later app built from the default
    config would demand it. ``monkeypatch.delenv`` records nothing for a
    variable that was absent, so this is an explicit snapshot instead.
    """
    names = (V0_SESSION_API_KEY_ENV, V1_SESSION_API_KEY_ENV)
    saved = {name: os.environ.get(name) for name in names}
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True)
def isolate_persistence_dir(tmp_path, monkeypatch):
    """Keep the developer's real ``~/.openhands`` out of every test.

    A server's stores (``ServerStores``) fall back to ``~/.openhands`` when
    ``OH_PERSISTENCE_DIR`` is unset, so without this an app built by a test
    would pick up whatever settings.json happens to be on the host and pass or
    fail depending on the machine.
    """
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    # ``ServerStores`` resolves ``OH_PERSISTENCE_DIR`` when it is built, but
    # three SDK defaults are frozen into module constants at import time and
    # therefore ignore the env var above once the module is already imported
    # (which it always is here). Pin them too, or a store constructed with no
    # explicit ``base_dir`` still lands in the developer's real ``~/.openhands``.
    from openhands.sdk.llm import llm_profile_store
    from openhands.sdk.profiles import agent_profile_store
    from openhands.sdk.skills import installed as installed_skills

    persistence_dir = tmp_path / ".openhands"
    monkeypatch.setattr(
        llm_profile_store, "_DEFAULT_PROFILE_DIR", persistence_dir / "profiles"
    )
    monkeypatch.setattr(
        agent_profile_store, "_DEFAULT_PROFILE_DIR", persistence_dir / "agent-profiles"
    )
    monkeypatch.setattr(
        installed_skills,
        "DEFAULT_INSTALLED_SKILLS_DIR",
        persistence_dir / "skills" / "installed",
    )


@pytest.fixture
def server_stores() -> ServerStores:
    """The stores of a server with the default config, in the test's own dir.

    For tests that build a ``ConversationService`` (or call a helper) directly
    rather than through an app: pass these in, and read or seed them, instead
    of expecting a process-wide store.
    """
    return ServerStores.from_config(Config())


def client_context(client: TestClient) -> ServerContext:
    """The server context of the app behind a ``TestClient``.

    Read it after the request (or the ``with`` block) whose effect a test
    checks: the lifespan and ``POST /api/init`` each install a new one.
    """
    app = client.app
    assert isinstance(app, Starlette)
    return server_context_of(app)


def update_test_context(app: Starlette, **changes: Any) -> ServerContext:
    """Rebind part of the context ``app`` already has, keeping the rest.

    For an app from ``create_app`` whose test swaps in a config or a service:
    ``update_test_context(app, config=other)`` leaves its stores, registry and
    services where they are.
    """
    context = replace(server_context_of(app), **changes)
    install_server_context(app, context)
    return context


def install_test_context(
    app: Starlette,
    config: Config | None = None,
    *,
    conversation_registry: ConversationRegistry | None = None,
    conversation_service: ConversationService | None = None,
    bash_event_service: BashEventService | None = None,
) -> ServerContext:
    """Give a hand-built app (a bare ``FastAPI`` plus routers) a server context.

    ``create_app`` installs one itself; this is for tests that mount a router
    on their own app and still need what its handlers read from the context.
    Services passed here stand in for the ones the lifespan would start, and a
    registry passed here brings its own config and stores. Without one the app
    gets the plain host-local registry whatever runtime ``config`` names, so a
    router test can describe a Docker deployment without a Docker registry.
    """
    if conversation_registry is not None:
        config = config if config is not None else conversation_registry.config
        stores = conversation_registry.stores
    else:
        config = config if config is not None else Config()
        stores = ServerStores.from_config(config)
        conversation_registry = ConversationRegistry(config, stores)
    context = ServerContext(
        config=config,
        stores=stores,
        conversation_registry=conversation_registry,
        conversation_service=conversation_service,
        bash_event_service=bash_event_service,
    )
    install_server_context(app, context)
    return context
