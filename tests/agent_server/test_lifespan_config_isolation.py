"""The non-deferred lifespan builds services from ``create_app(config)``.

Regression coverage for #5590: the non-deferred lifespan must build the
conversation and bash event services from the ``Config`` the app was created
with, never from ``get_default_config()``/``OH_*``. Otherwise an in-process app
(while the environment points at another store, e.g. a live Cloud runtime
sandbox exporting ``OH_CONVERSATIONS_PATH``) writes conversations into the
env-derived store instead of ``config.conversations_path``.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

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
from openhands.agent_server.bash_service import get_default_bash_event_service
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import get_default_conversation_service
from openhands.agent_server.models import ExecuteBashRequest, StartConversationRequest
from openhands.agent_server.persistence import reset_stores
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


def _bash_event_files(path: Path) -> list[str]:
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


def test_bash_websocket_routes_to_the_config_built_service(tmp_path, monkeypatch):
    """A real bash-events WebSocket must use the app's Config-backed service.

    Observable behavior: a command sent over the socket is written under
    ``config.bash_events_dir``, not the env-derived default directory.
    """
    host_bash = tmp_path / "host" / "bash_events"
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(host_bash))
    monkeypatch.setattr(config_module, "_default_config", None)

    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)
    app = create_app(config)

    with TestClient(app) as client:
        with client.websocket_connect("/sockets/bash-events") as ws:
            ws.send_json(
                {"command": "echo ws-routed", "cwd": str(config.workspace_path)}
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if any(
                    "BashOutput" in name
                    for name in _bash_event_files(config.bash_events_dir)
                ):
                    break
                time.sleep(0.05)

    assert any(
        "BashOutput" in name for name in _bash_event_files(config.bash_events_dir)
    )
    assert not host_bash.exists()


def test_explicit_config_app_does_not_commandeer_default_getters(tmp_path):
    """An explicit-config app must not hijack the process-default getters.

    Those getters back a concurrently running default app; the explicit app
    keeps its own services on ``app.state``.
    """
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)

    default_conversation_service = get_default_conversation_service()
    default_bash_service = get_default_bash_event_service()

    app = create_app(config)
    with TestClient(app) as client:
        client.get("/ready")
        assert app.state.conversation_service.conversations_dir == (
            config.conversations_path
        )
        assert app.state.bash_event_service.bash_events_dir == config.bash_events_dir

    assert get_default_conversation_service() is default_conversation_service
    assert get_default_bash_event_service() is default_bash_service


def test_explicit_process_default_config_app_owns_default_getters(
    tmp_path, monkeypatch
):
    """Passing the process-default Config explicitly must not split services.

    ``create_app(get_default_config())`` uses the very same Config the no-arg
    factory would, so it owns the default getters rather than leaving them on a
    detached instance.
    """
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(tmp_path / "conv"))
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(tmp_path / "bash"))
    monkeypatch.setattr(config_module, "_default_config", None)

    default_config = config_module.get_default_config()
    app = create_app(default_config)
    assert app.state.owns_default_singletons is True

    with TestClient(app) as client:
        client.get("/ready")
        assert get_default_conversation_service() is app.state.conversation_service
        assert get_default_bash_event_service() is app.state.bash_event_service


def test_overlapping_default_apps_restore_the_running_app(tmp_path, monkeypatch):
    """Whichever default app stops, the survivor keeps owning the getters.

    Two default-config apps publish to the same singletons. Stopping either one
    must leave the getters on the still-running app instead of constructing
    services outside any lifespan — covering both shutdown orders.
    """
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(tmp_path / "conv"))
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(tmp_path / "bash"))
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "tmux"))
    monkeypatch.setattr(config_module, "_default_config", None)

    app_a = create_app()
    app_b = create_app()
    assert app_a.state.config is app_b.state.config

    for stop_first, survivor in ((app_b, app_a), (app_a, app_b)):
        client_a = TestClient(app_a).__enter__()
        client_b = TestClient(app_b).__enter__()
        client_a.get("/ready")
        client_b.get("/ready")
        survivor_service = survivor.state.conversation_service
        survivor_bash = survivor.state.bash_event_service
        try:
            assert get_default_conversation_service() is (
                app_b.state.conversation_service
            )
            stop_first_client = client_a if stop_first is app_a else client_b
            stop_first_client.__exit__(None, None, None)
            # The survivor's services must be republished to the getters.
            assert get_default_conversation_service() is survivor_service
            assert get_default_bash_event_service() is survivor_bash
        finally:
            client_a.__exit__(None, None, None)
            client_b.__exit__(None, None, None)
        # Neither app's services remain once both have stopped.
        assert get_default_conversation_service() is not survivor_service
        assert get_default_bash_event_service() is not survivor_bash


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


async def test_lifespan_closes_bash_service_and_kills_running_commands(tmp_path):
    """Commands must not outlive the app that started them.

    A long-running command is started through the lifespan's bash service;
    leaving the lifespan must close the service, killing the command's process
    group and refusing new work.
    """
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)
    app = create_app(config)
    pid_file = tmp_path / "child.pid"

    async with api_lifespan(app):
        bash_svc = app.state.bash_event_service
        await bash_svc.start_bash_command(
            ExecuteBashRequest(
                command=f"echo $$ > {pid_file}; exec sleep 300",
                cwd=str(config.workspace_path),
            )
        )
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert pid_file.exists(), "command never started"
        pid = int(pid_file.read_text().strip())

    # The process group must be gone once the app has shut down.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)

    # The closed service must reject new work.
    with pytest.raises(RuntimeError, match="closed"):
        await bash_svc.start_bash_command(
            ExecuteBashRequest(command="echo late", cwd=str(config.workspace_path))
        )


async def test_bash_is_closed_when_another_shutdown_step_raises(tmp_path, monkeypatch):
    """Bash teardown must run even if an earlier shutdown step fails.

    If ``conversation_registry.shutdown()`` raises, the running command's process
    group must still be killed rather than outliving the app.
    """
    config = _config(tmp_path)
    config.workspace_path.mkdir(parents=True)
    app = create_app(config)
    pid_file = tmp_path / "child.pid"

    async def boom():
        raise RuntimeError("registry shutdown failed")

    with pytest.raises(RuntimeError, match="registry shutdown failed"):
        async with api_lifespan(app):
            bash_svc = app.state.bash_event_service
            monkeypatch.setattr(app.state.conversation_registry, "shutdown", boom)
            await bash_svc.start_bash_command(
                ExecuteBashRequest(
                    command=f"echo $$ > {pid_file}; exec sleep 300",
                    cwd=str(config.workspace_path),
                )
            )
            deadline = time.monotonic() + 10
            while not pid_file.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            assert pid_file.exists(), "command never started"

    pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


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
