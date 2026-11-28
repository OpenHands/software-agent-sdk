"""Installed plugins through a real per-conversation Docker runtime.

Set OPENHANDS_DOCKER_PLUGIN_TEST_IMAGE to an Agent Server image built from the
checkout under test. Messages do not run the LLM; plugin loading is real.
"""

import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry
from openhands.agent_server.server_context import server_context_of


pytestmark = pytest.mark.docker_live


@pytest.fixture
def mcp_url(tmp_path: Path, unused_tcp_port: int) -> Iterator[str]:
    script = (
        "import sys\nfrom fastmcp import FastMCP\n"
        "mcp = FastMCP('runtime-probe')\n"
        "@mcp.tool()\ndef probe() -> str:\n    return 'MCP_PROBE'\n"
        "mcp.run(transport='http', host='0.0.0.0', "
        "port=int(sys.argv[1]), show_banner=False)\n"
    )
    with (tmp_path / "mcp.log").open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(unused_tcp_port)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, (tmp_path / "mcp.log").read_text()
                try:
                    with socket.create_connection(("127.0.0.1", unused_tcp_port)):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "MCP server did not start"
                    time.sleep(0.1)
            yield f"http://host.docker.internal:{unused_tcp_port}/mcp"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.fixture
def plugin_source(tmp_path: Path, mcp_url: str) -> Path:
    plugin = tmp_path / "source"
    for directory in (".plugin", "skills/probe", "commands", "hooks"):
        (plugin / directory).mkdir(parents=True)
    (plugin / ".plugin/plugin.json").write_text(
        json.dumps({"name": "runtime-probe", "version": "1.0.0"})
    )
    (plugin / "skills/probe/SKILL.md").write_text(
        "---\nname: runtime-probe-skill\ndescription: Runtime probe\n---\n"
        "The installed plugin is available.\n"
    )
    (plugin / "commands/ping.md").write_text(
        "---\ndescription: Run the installed command\n---\nReply with pong.\n"
    )
    (plugin / "hooks/hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "echo PLUGIN_HOOK"}]}
                    ]
                }
            }
        )
    )
    (plugin / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"runtime-probe-mcp": {"url": mcp_url}}})
    )
    return plugin


@pytest.fixture(params=[False, True], ids=["docker", "hybrid"])
def plugin_client(tmp_path: Path, monkeypatch, request) -> Iterator[TestClient]:
    image = os.environ.get("OPENHANDS_DOCKER_PLUGIN_TEST_IMAGE")
    if not image:
        pytest.skip("Set OPENHANDS_DOCKER_PLUGIN_TEST_IMAGE to run real Docker")
    subprocess.run(
        ["docker", "image", "inspect", image], check=True, capture_output=True
    )
    persistence = tmp_path / "persistence"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence))
    monkeypatch.setattr(
        "openhands.sdk.plugin.installed.DEFAULT_INSTALLED_PLUGINS_DIR",
        persistence / "plugins" / "installed",
    )
    app = create_app(
        Config(
            conversation_runtime="docker",
            conversation_runtime_selectable=request.param,
            conversation_image=image,
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspace",
            secret_key=SecretStr("test-outer-key"),
            conversation_container_memory="2g",
            conversation_container_cpus=1,
            enable_browser=False,
        )
    )
    registry = server_context_of(app).conversation_registry
    assert isinstance(registry, DockerConversationRegistry)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        registry.cleanup_stale_containers()


def _start(client: TestClient, **fields) -> str:
    response = client.post(
        "/api/conversations",
        json={
            "agent": {
                "kind": "Agent",
                "llm": {"model": "gpt-4o", "api_key": "unused-test-key"},
                "tools": [],
            },
            "autotitle": False,
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _send(client: TestClient, cid: str, text: str = "hello") -> dict:
    response = client.post(
        f"/api/conversations/{cid}/events",
        json={"content": [{"type": "text", "text": text}], "run": False},
    )
    assert response.status_code == 200, response.text
    response = client.get(f"/api/conversations/{cid}?include_skills=true")
    assert response.status_code == 200, response.text
    return response.json()


def _skill_names(conversation: dict) -> set[str]:
    context = conversation["agent"].get("agent_context") or {}
    return {skill["name"] for skill in context.get("skills", [])}


@pytest.mark.parametrize("enabled", [True, False])
def test_docker_loads_only_enabled_installed_plugins(
    plugin_client: TestClient, plugin_source: Path, enabled: bool
):
    client = plugin_client
    response = client.post("/api/plugins/install", json={"source": str(plugin_source)})
    assert response.status_code == 200, response.text
    response = client.patch(
        "/api/plugins/installed/runtime-probe", json={"enabled": enabled}
    )
    assert response.status_code == 200, response.text
    cid = _start(client)
    conversation = _send(client, cid, "/runtime-probe:ping")
    names = _skill_names(conversation)
    assert ("runtime-probe-skill" in names) is enabled, conversation
    assert ("runtime-probe:ping" in names) is enabled, conversation
    assert ("runtime-probe-mcp" in conversation["agent"]["mcp_config"]) is enabled
    response = client.get(f"/api/conversations/{cid}/events/search")
    assert response.status_code == 200, response.text
    events = response.json()["items"]
    hooks = [event for event in events if event["kind"] == "HookExecutionEvent"]
    assert any("PLUGIN_HOOK" in json.dumps(event) for event in hooks) is enabled
    messages = [event for event in events if event["kind"] == "MessageEvent"]
    assert (
        any(
            "runtime-probe:ping" in event.get("activated_skills", [])
            for event in messages
        )
        is enabled
    )
    response = client.post(
        f"/api/conversations/{cid}/bash/execute_bash_command",
        json={"command": f"test ! -e {shlex.quote(str(plugin_source))}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["exit_code"] == 0, response.text


@pytest.mark.parametrize("remove", [False, True], ids=["disable", "uninstall"])
def test_docker_resume_rechecks_enabled_plugins(
    plugin_client: TestClient, plugin_source: Path, remove: bool
):
    client = plugin_client
    response = client.post("/api/plugins/install", json={"source": str(plugin_source)})
    assert response.status_code == 200, response.text
    cid = _start(client)
    assert "runtime-probe-skill" in _skill_names(_send(client, cid))
    response = (
        client.delete("/api/plugins/installed/runtime-probe")
        if remove
        else client.patch(
            "/api/plugins/installed/runtime-probe", json={"enabled": False}
        )
    )
    assert response.status_code == 200, response.text
    assert "runtime-probe-skill" in _skill_names(_send(client, cid))
    response = client.delete(f"/api/conversations/{cid}/runtime")
    assert response.status_code == 204, response.text
    response = client.post(f"/api/conversations/{cid}/runtime/reprovision")
    assert response.status_code == 200, response.text
    resumed = _send(client, cid)
    assert "runtime-probe-skill" not in _skill_names(resumed)
    assert "runtime-probe-mcp" not in resumed["agent"]["mcp_config"]


def test_explicit_plugin_wins_and_remains_attached_after_disable(
    plugin_client: TestClient, plugin_source: Path, tmp_path: Path
):
    client = plugin_client
    response = client.post("/api/plugins/install", json={"source": str(plugin_source)})
    assert response.status_code == 200, response.text
    workspace = tmp_path / "explicit-workspace"
    explicit = workspace / "plugin"
    shutil.copytree(plugin_source, explicit)
    (explicit / "skills/probe/SKILL.md").write_text(
        "---\nname: explicit-skill\ndescription: Explicit plugin\n---\nExplicit.\n"
    )
    cid = _start(
        client,
        plugins=[{"source": "/workspace/plugin"}],
        workspace={"kind": "LocalWorkspace", "working_dir": str(workspace)},
    )
    conversation = _send(client, cid)
    assert "explicit-skill" in _skill_names(conversation)
    assert "runtime-probe-skill" not in _skill_names(conversation)
    response = client.patch(
        "/api/plugins/installed/runtime-probe", json={"enabled": False}
    )
    assert response.status_code == 200, response.text
    assert client.delete(f"/api/conversations/{cid}/runtime").status_code == 204
    assert (
        client.post(f"/api/conversations/{cid}/runtime/reprovision").status_code == 200
    )
    assert "explicit-skill" in _skill_names(_send(client, cid))
