"""Storage reclaim against a real Docker daemon and agent-server image.

See STORAGE_SCENARIO.md next to this file.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry


pytestmark = pytest.mark.docker_live

IMAGE = os.environ.get(
    "OH_STORAGE_SCENARIO_IMAGE", "ghcr.io/openhands/agent-server:latest-python"
)

# Written by the sandbox itself, as installs would: caches in its $HOME, a
# git-ignored dependency dir and a tracked file in its workspace.
FILL = """
set -e
mkdir -p "$HOME/.cache/uv" "$HOME/.npm/_cacache"
head -c 1048576 /dev/urandom > "$HOME/.cache/uv/wheel.whl"
echo cached > "$HOME/.npm/_cacache/index"
cd /workspace
git init -q
echo node_modules/ > .gitignore
mkdir -p node_modules/left-pad
echo 'module.exports = 1' > node_modules/left-pad/index.js
echo 'print(1)' > main.py
"""


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    info = subprocess.run(["docker", "info"], capture_output=True, check=False)
    if info.returncode != 0:
        return False
    image = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
    )
    return image.returncode == 0


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    if not _docker_ready():
        pytest.skip(f"needs a running Docker daemon and the image {IMAGE}")
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    app = create_app(
        Config(
            conversation_runtime="docker",
            conversation_image=IMAGE,
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("outer-key"),
            static_files_path=None,
        )
    )
    registry: DockerConversationRegistry = app.state.conversation_registry
    try:
        with TestClient(app) as client:
            yield client
    finally:
        registry.cleanup_stale_containers()


def _registry(client: TestClient) -> DockerConversationRegistry:
    return client.app.state.conversation_registry  # type: ignore[attr-defined]


def _start(client: TestClient) -> UUID:
    response = client.post(
        "/api/conversations",
        json={"agent": {"kind": "Agent", "llm": {"model": "test"}, "tools": []}},
    )
    assert response.status_code == 201, response.text
    conversation_id = UUID(response.json()["id"])
    container = _registry(client).get(conversation_id)
    assert container is not None
    subprocess.run(
        ["docker", "exec", container.container_id, "sh", "-c", FILL],
        check=True,
        capture_output=True,
    )
    return conversation_id


def _home(client: TestClient, conversation_id: UUID) -> Path:
    return _registry(client).provisioning.runtime_dir(conversation_id) / "persistence"


def _workspace(client: TestClient, conversation_id: UUID) -> Path:
    return _registry(client).provisioning.load(conversation_id).workspace_path


def test_stopped_runtime_loses_caches_then_dependencies_over_budget(client):
    registry = _registry(client)
    reclaimer = registry.reclaimer
    stopped, running = _start(client), _start(client)

    response = client.delete(f"/api/conversations/{stopped}/runtime")
    assert response.status_code == 204, response.text
    client.portal.call(reclaimer.trash.drain)

    # Stopping drops only the caches; the workspace is untouched.
    assert not (_home(client, stopped) / ".cache").exists()
    assert not (_home(client, stopped) / ".npm").exists()
    assert (_workspace(client, stopped) / "node_modules" / "left-pad").is_dir()
    assert (_workspace(client, stopped) / "main.py").is_file()
    # The other conversation is still running and keeps everything.
    assert registry.get(running) is not None
    assert (_home(client, running) / ".cache" / "uv" / "wheel.whl").is_file()
    assert (_workspace(client, running) / "node_modules" / "left-pad").is_dir()

    # Over budget (any real disk is more than 1% full), one pass sheds the
    # git-ignored dependencies of stopped runtimes only.
    reclaimer.disk_budget = 0.01
    client.portal.call(reclaimer.run_pass)

    assert not (_workspace(client, stopped) / "node_modules").exists()
    assert (_workspace(client, stopped) / "main.py").is_file()
    assert (_workspace(client, stopped) / ".gitignore").is_file()
    assert (_workspace(client, running) / "node_modules" / "left-pad").is_dir()
    assert (_home(client, running) / ".cache" / "uv" / "wheel.whl").is_file()
    assert list(reclaimer.trash.dir.iterdir()) == []
