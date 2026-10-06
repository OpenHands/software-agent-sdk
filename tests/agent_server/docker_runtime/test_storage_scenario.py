"""Storage reclaim against a real Docker daemon and agent-server image.

See STORAGE_SCENARIO.md next to this file.
"""

import hashlib
import os
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import (
    config as config_module,
    conversation_service as service_module,
)
from openhands.agent_server.api import create_app
from openhands.agent_server.docker_runtime.registry import DockerConversationRegistry


pytestmark = pytest.mark.docker_live

IMAGE = os.environ.get(
    "OH_STORAGE_SCENARIO_IMAGE", "ghcr.io/openhands/agent-server:latest-python"
)
GIT = "git -c user.name=agent -c user.email=agent@example.com"

# What a sandbox leaves after real work: caches and dependencies it can
# rebuild, next to things that are someone's work or state.
HOME_FILL = """
set -e
mkdir -p "$HOME/.cache/uv" "$HOME/.npm/_cacache" "$HOME/.config/tool"
head -c 1048576 /dev/urandom > "$HOME/.cache/uv/wheel.whl"
echo cached > "$HOME/.npm/_cacache/index"
echo 'npm install' > "$HOME/.bash_history"
echo 'key = 1' > "$HOME/.config/tool/settings.toml"
"""
WORKSPACE_FILL = f"""
set -e
cd /workspace
git init -q
printf 'node_modules/\\n.venv/\\nbuild/\\n.env\\nvendor/\\n' > .gitignore
echo 'print(1)' > main.py
{GIT} add .gitignore main.py && {GIT} commit -qm init
echo 'draft' > notes.md
echo 'TOKEN=secret' > .env
mkdir -p node_modules/left-pad .venv/lib build
echo 1 > node_modules/left-pad/index.js
echo 1 > .venv/lib/site.py
echo 1 > build/out.o
mkdir -p vendor/lib && cd vendor/lib && git init -q && echo mine > lib.c
cd /workspace && mkdir -p sub && cd sub && git init -q
echo node_modules/ > .gitignore && echo 1 > app.js
mkdir -p node_modules/dep && echo 1 > node_modules/dep/i.js
"""
FILL = HOME_FILL + WORKSPACE_FILL

SHED_ON_STOP = (".cache", ".npm")
KEPT_IN_HOME = (".bash_history", ".config/tool/settings.toml")
SHED_OVER_BUDGET = ("node_modules", ".venv", "build", "sub/node_modules")
KEPT_IN_WORKSPACE = (
    ".git/HEAD",
    ".gitignore",
    "main.py",
    "notes.md",
    ".env",
    "vendor/lib/lib.c",
    "vendor/lib/.git/HEAD",
    "sub/app.js",
    "sub/.gitignore",
)


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return False
    inspect = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True)
    return inspect.returncode == 0


@pytest.fixture
def server(tmp_path, monkeypatch) -> Callable[..., AbstractContextManager[TestClient]]:
    """Start the server the way the CLI does: configured by environment."""
    if not _docker_ready():
        pytest.skip(f"needs a running Docker daemon and the image {IMAGE}")

    @contextmanager
    def start(**env: str) -> Iterator[TestClient]:
        for name, value in {
            "OH_CONVERSATION_RUNTIME": "docker",
            "OH_CONVERSATION_IMAGE": IMAGE,
            "OH_CONVERSATIONS_PATH": str(tmp_path / "conversations"),
            "OH_PERSISTENCE_DIR": str(tmp_path / "persistence"),
            "OH_SECRET_KEY": "outer-key",
            **env,
        }.items():
            monkeypatch.setenv(name, value)
        # Both are process-wide and would keep another test's paths.
        monkeypatch.setattr(config_module, "_default_config", None)
        monkeypatch.setattr(service_module, "_conversation_service", None)
        app = create_app()
        try:
            with TestClient(app) as client:
                yield client
        finally:
            app.state.conversation_registry.cleanup_stale_containers()

    return start


def _registry(client: TestClient) -> DockerConversationRegistry:
    return client.app.state.conversation_registry  # type: ignore[attr-defined]


def _start(client: TestClient, working_dir: Path | None = None) -> UUID:
    body: dict = {"agent": {"kind": "Agent", "llm": {"model": "test"}, "tools": []}}
    if working_dir is not None:
        body["workspace"] = {"kind": "LocalWorkspace", "working_dir": str(working_dir)}
    response = client.post("/api/conversations", json=body)
    assert response.status_code == 201, response.text
    return UUID(response.json()["id"])


def _exec(client: TestClient, conversation_id: UUID, script: str) -> str:
    container = _registry(client).get(conversation_id)
    assert container is not None, "the conversation's container is not running"
    result = subprocess.run(
        ["docker", "exec", container.container_id, "sh", "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _call(client: TestClient, fn: Callable[[], Awaitable[object]]) -> None:
    """Run a coroutine on the server's event loop."""
    assert client.portal is not None
    client.portal.call(fn)


def _stop(client: TestClient, conversation_id: UUID) -> None:
    response = client.delete(f"/api/conversations/{conversation_id}/runtime")
    assert response.status_code == 204, response.text
    _call(client, _registry(client).reclaimer.trash.drain)


def _pass(client: TestClient) -> None:
    reclaimer = _registry(client).reclaimer
    _call(client, reclaimer.run_pass)
    _call(client, reclaimer.trash.drain)


def _home(client: TestClient, conversation_id: UUID) -> Path:
    return _registry(client).provisioning.runtime_dir(conversation_id) / "persistence"


def _workspace(client: TestClient, conversation_id: UUID) -> Path:
    return _registry(client).provisioning.load(conversation_id).workspace_path


def _snapshot(root: Path) -> dict[str, str]:
    """Every file and link under ``root``, with its content hash or target."""
    snapshot = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[key] = "-> " + os.readlink(path)
        elif path.is_file():
            snapshot[key] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_stop_and_budget_drop_only_what_a_runtime_can_rebuild(server):
    with server(OH_CONVERSATION_STORAGE_DISK_BUDGET="0.01") as client:
        stopped, running = _start(client), _start(client)
        for conversation_id in (stopped, running):
            _exec(client, conversation_id, FILL)
        running_dir = _registry(client).provisioning.runtime_dir(running)
        before = _snapshot(running_dir)

        _stop(client, stopped)

        home, workspace = _home(client, stopped), _workspace(client, stopped)
        for name in SHED_ON_STOP:
            assert not os.path.lexists(home / name), name
        for name in KEPT_IN_HOME:
            assert (home / name).is_file(), name
        for name in (*SHED_OVER_BUDGET, *KEPT_IN_WORKSPACE):
            assert (workspace / name).exists(), f"{name} must survive a stop"

        # Any real disk is more than 1% full, so the pass is over budget.
        _pass(client)

        for name in SHED_OVER_BUDGET:
            assert not (workspace / name).exists(), f"{name} should be shed"
        for name in KEPT_IN_WORKSPACE:
            assert (workspace / name).is_file(), f"{name} must never be shed"
        for name in KEPT_IN_HOME:
            assert (home / name).is_file(), name
        # The running conversation is untouched, file for file.
        assert _registry(client).get(running) is not None
        assert _snapshot(running_dir) == before
        assert list(_registry(client).reclaimer.trash.dir.iterdir()) == []


def test_links_planted_by_the_sandbox_are_removed_not_followed(server, tmp_path):
    # Paths outside the runtime that a sandbox could guess and point at.
    outside = tmp_path / "outside"
    for name in ("npm", "build"):
        (outside / name).mkdir(parents=True)
        (outside / name / "precious").write_text("keep")
    with server(OH_CONVERSATION_STORAGE_DISK_BUDGET="0.01") as client:
        conversation_id = _start(client)
        _exec(
            client,
            conversation_id,
            f"""
            set -e
            mkdir -p /workspace/precious && echo keep > /workspace/precious/data
            ln -s {outside}/npm "$HOME/.npm"
            # Relative: inside the sandbox it dangles, on the host it lands in
            # the runtime's own workspace.
            # The server creates ~/.cache at boot; replace it with the link.
            rm -rf "$HOME/.cache"
            ln -s ../workspace/precious "$HOME/.cache"
            cd /workspace && git init -q && echo build/ > .gitignore
            ln -s {outside}/build build
            """,
        )
        home = _home(client, conversation_id)
        workspace = _workspace(client, conversation_id)
        assert (home / ".cache" / "data").is_file()  # it does resolve on the host

        _stop(client, conversation_id)
        _pass(client)

        assert not os.path.lexists(home / ".cache")
        assert not os.path.lexists(home / ".npm")
        assert (workspace / "precious" / "data").read_text() == "keep\n"
        for name in ("npm", "build"):
            assert (outside / name / "precious").read_text() == "keep"


def test_a_caller_supplied_workspace_is_never_shed(server, tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "node_modules" / "dep").mkdir(parents=True)
    (checkout / "node_modules" / "dep" / "i.js").write_text("1")
    (checkout / ".gitignore").write_text("node_modules/\n")
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    with server(OH_CONVERSATION_STORAGE_DISK_BUDGET="0.01") as client:
        conversation_id = _start(client, working_dir=checkout)
        _exec(client, conversation_id, HOME_FILL)

        _stop(client, conversation_id)
        _pass(client)

        assert (checkout / "node_modules" / "dep" / "i.js").read_text() == "1"
        # The sandbox's home is still the server's, so its caches go.
        assert not (_home(client, conversation_id) / ".cache").exists()


def test_a_reclaimed_conversation_resumes_and_works(server):
    with server(OH_CONVERSATION_STORAGE_DISK_BUDGET="0.01") as client:
        conversation_id = _start(client)
        _exec(client, conversation_id, FILL)
        _stop(client, conversation_id)
        _pass(client)
        assert _registry(client).get(conversation_id) is None

        # Reading a workspace file through the API starts the container again.
        response = client.get(f"/api/conversations/{conversation_id}/workspace/main.py")
        assert response.status_code == 200, response.text
        assert response.text == "print(1)\n"
        assert _registry(client).get(conversation_id) is not None

        out = _exec(
            client,
            conversation_id,
            """
            set -e
            cat /workspace/main.py
            git -C /workspace log --format=%s -1
            mkdir -p "$HOME/.cache/uv" && echo rebuilt > "$HOME/.cache/uv/new"
            mkdir -p /workspace/node_modules/again
            echo ok
            """,
        )
        assert out.split() == ["print(1)", "init", "ok"]


def test_an_archived_conversation_is_read_only_until_deleted(server):
    with server(OH_CONVERSATION_STORAGE_RETENTION_DAYS="1") as client:
        registry = _registry(client)
        old, recent = _start(client), _start(client)
        for conversation_id in (old, recent):
            _exec(client, conversation_id, FILL)
            _stop(client, conversation_id)
        state = registry.conversation_dir(old) / "base_state.json"
        two_days_ago = time.time() - 2 * 86400
        os.utime(state, (two_days_ago, two_days_ago))

        _pass(client)

        assert registry.is_archived(old)
        assert not registry.provisioning.runtime_dir(old).exists()
        assert not registry.is_archived(recent)
        assert (_workspace(client, recent) / "main.py").is_file()
        # History stays readable; the runtime cannot come back.
        assert client.get(f"/api/conversations/{old}").status_code == 200
        events = client.get(f"/api/conversations/{old}/events/search")
        assert events.status_code == 200, events.text
        response = client.get(f"/api/conversations/{old}/workspace/main.py")
        assert response.status_code == 410, response.text
        assert registry.get(old) is None

        assert client.delete(f"/api/conversations/{old}").status_code in (200, 204)
        assert not registry.is_archived(old)
        assert not registry.conversation_dir(old).exists()
