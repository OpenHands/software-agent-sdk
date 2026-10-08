"""The non-deferred lifespan builds services from ``create_app(config)``.

Regression coverage for #5590: ``api_lifespan`` used to call the process-global
``get_default_conversation_service()`` / ``get_default_bash_event_service()``,
which resolve ``get_default_config()`` from ``OH_*`` environment variables. When
an agent-server runs in-process with an explicit ``Config`` while the environment
points at a different store (e.g. a live Cloud runtime sandbox exporting
``OH_CONVERSATIONS_PATH``), conversations were written into the env-derived store
instead of ``config.conversations_path``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server import (
    bash_service as bash_service_module,
    config as config_module,
    conversation_service as conversation_service_module,
)
from openhands.agent_server.api import api_lifespan, create_app
from openhands.agent_server.config import Config
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.persistence import reset_stores
from openhands.agent_server.sockets import (
    _get_bash_event_service,
    _get_conversation_service,
)
from openhands.sdk import LLM, Agent
from openhands.sdk.workspace import LocalWorkspace


@pytest.fixture(autouse=True)
def _isolate_process_globals(monkeypatch):
    """Pin the env-derived default config and the service singletons.

    ``get_default_config()`` caches the first config it builds, and the module
    singletons cache the first service. Clear both so each test observes the
    environment it sets up rather than another test's leftovers. ``TMUX_TMPDIR``
    is removed because the lifespan cleans stale tmux sockets in it, which must
    not touch the host's tmux server.
    """
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setattr(config_module, "_default_config", None)
    conversation_service_module._conversation_service = None
    bash_service_module._bash_event_service = None
    reset_stores()
    try:
        yield
    finally:
        conversation_service_module._conversation_service = None
        bash_service_module._bash_event_service = None
        reset_stores()


def _config(tmp_path: Path) -> Config:
    return Config(
        static_files_path=None,
        session_api_keys=[],
        secret_key=SecretStr("isolation-key"),
        conversations_path=tmp_path / "isolated" / "conversations",
        bash_events_dir=tmp_path / "isolated" / "bash_events",
        workspace_path=tmp_path / "isolated" / "workspace",
    )


def _start_request(workspace: Path) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="isolation-model"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace)),
    )


def _start_conversation(client: TestClient, workspace: Path) -> str:
    response = client.post(
        "/api/conversations",
        json={
            "agent": {
                "kind": "Agent",
                "llm": {"model": "isolation-model"},
                "tools": [],
            },
            "workspace": {"kind": "LocalWorkspace", "working_dir": str(workspace)},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _conversation_dirs(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir()) if path.exists() else []


def test_conversation_stored_under_config_path_without_env(tmp_path):
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)

    with TestClient(create_app(config)) as client:
        _start_conversation(client, config.workspace_path)

    assert len(_conversation_dirs(config.conversations_path)) == 1


def test_conversation_stays_isolated_when_env_points_elsewhere(tmp_path, monkeypatch):
    host_conversations = tmp_path / "host" / "conversations"
    host_bash = tmp_path / "host" / "bash_events"
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(host_conversations))
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(host_bash))
    monkeypatch.setattr(config_module, "_default_config", None)

    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)

    with TestClient(create_app(config)) as client:
        _start_conversation(client, config.workspace_path)

    assert len(_conversation_dirs(config.conversations_path)) == 1
    assert _conversation_dirs(host_conversations) == []


def test_bash_events_written_under_config_dir_when_env_points_elsewhere(
    tmp_path, monkeypatch
):
    host_bash = tmp_path / "host" / "bash_events"
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(host_bash))
    monkeypatch.setattr(config_module, "_default_config", None)

    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)

    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/bash/execute_bash_command",
            json={"command": "echo isolation", "cwd": str(config.workspace_path)},
        )
        assert response.status_code == 200, response.text

    assert config.bash_events_dir.exists()
    assert any(config.bash_events_dir.iterdir())
    assert not host_bash.exists()


def test_create_app_without_argument_uses_environment_default(tmp_path, monkeypatch):
    env_conversations = tmp_path / "env" / "conversations"
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(env_conversations))
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(tmp_path / "env" / "bash_events"))
    monkeypatch.setattr(config_module, "_default_config", None)

    app = create_app()
    with TestClient(app) as client:
        client.get("/ready")
        assert app.state.conversation_service.conversations_dir == env_conversations


def test_rest_service_uses_config_and_publishes_matching_singletons(tmp_path):
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)

    app = create_app(config)
    with TestClient(app) as client:
        client.get("/ready")
        assert app.state.conversation_service.conversations_dir == (
            config.conversations_path
        )
        assert app.state.bash_event_service.bash_events_dir == config.bash_events_dir

        # Import-time callers (sockets.py) read the module singletons; they must
        # resolve the same instances REST handlers operate on.
        assert (
            conversation_service_module._conversation_service
            is app.state.conversation_service
        )
        assert bash_service_module._bash_event_service is app.state.bash_event_service

        ws = MagicMock()
        ws.app = app
        assert _get_conversation_service(ws) is app.state.conversation_service
        assert _get_bash_event_service(ws) is app.state.bash_event_service


async def test_lifespan_exits_the_config_built_conversation_service(tmp_path):
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)
    app = create_app(config)

    async with api_lifespan(app):
        service = app.state.conversation_service
        info, _ = await service.start_conversation(
            _start_request(config.workspace_path)
        )
        assert info.id is not None

    # Leaving the lifespan must exit the service it entered.
    with pytest.raises(ValueError, match="inactive_service"):
        await service.start_conversation(_start_request(config.workspace_path))


def test_deferred_init_lifespan_does_not_publish_service_singletons(tmp_path):
    config = Config(
        deferred_init=True,
        conversations_path=tmp_path / "convs",
        bash_events_dir=tmp_path / "bash",
    )

    app: FastAPI = create_app(config)
    with TestClient(app) as client:
        client.get("/ready")
        # Dormant mode leaves service construction to POST /api/init.
        assert getattr(app.state, "conversation_service", None) is None
        assert conversation_service_module._conversation_service is None
        assert bash_service_module._bash_event_service is None
        assert app.state.init_service.state == "dormant"

    assert os.environ.get("TMUX_TMPDIR") is None
