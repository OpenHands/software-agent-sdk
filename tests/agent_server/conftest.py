import os
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from openhands.agent_server.config import (
    V0_SESSION_API_KEY_ENV,
    V1_SESSION_API_KEY_ENV,
)
from openhands.agent_server.persistence import reset_stores
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

    ``_start_conversation`` reads the settings store on every launch, not just
    the ``agent_profile_id`` one, and ``get_settings_store`` falls back to
    ``~/.openhands`` when ``OH_PERSISTENCE_DIR`` is unset. Tests that build a
    ``ConversationService`` directly never initialise the singleton, so without
    this they would pick up whatever settings.json happens to be on the host and
    pass or fail depending on the machine.
    """
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / ".openhands"))
    # The singleton getters resolve ``OH_PERSISTENCE_DIR`` per call, but three
    # SDK defaults are frozen into module constants at import time and therefore
    # ignore the env var above once the module is already imported (which it
    # always is here). Pin them too, or a store constructed with no explicit
    # ``base_dir`` still lands in the developer's real ``~/.openhands``.
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
    reset_stores()
    try:
        yield
    finally:
        reset_stores()
