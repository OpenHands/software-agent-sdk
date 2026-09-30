import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from openhands.agent_server.config import Config
from openhands.agent_server.docker_runtime import registry as registry_module
from openhands.agent_server.docker_runtime.registry import (
    ConversationContainer,
    DockerConversationRegistry,
)


def registry(tmp_path, monkeypatch) -> DockerConversationRegistry:
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    return DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            workspace_path=tmp_path / "workspaces",
            secret_key=SecretStr("outer-key"),
            conversation_runtime_disk_budget=0.8,
        )
    )


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(repo: Path) -> tuple[list[Path], list[Path]]:
    """A checkout with deps installed; returns (rebuildable dirs, kept files)."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q")
    files = {
        ".gitignore": "node_modules/\n.venv/\n.env\n",
        "main.py": "print('hi')",
        "node_modules/left-pad/index.js": "module.exports = 1",
        ".venv/pyvenv.cfg": "home = /usr",
        # Untracked, not ignored: the agent's own work.
        "notes.md": "todo",
        # Ignored, but a file: may hold settings nobody can rebuild.
        ".env": "TOKEN=x",
    }
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
    git(repo, "add", ".gitignore", "main.py")
    shed = [repo / "node_modules", repo / ".venv"]
    kept = [repo / ".gitignore", repo / "main.py", repo / "notes.md", repo / ".env"]
    return shed, kept


def provision(
    runtime: DockerConversationRegistry, age: float, workspace: Path | None = None
) -> tuple[UUID, Path]:
    conversation_id = uuid4()
    identity = runtime.provisioning.create(conversation_id, workspace)
    # A started container has created its persistence dir, whatever the workspace.
    persistence = runtime.provisioning.runtime_dir(conversation_id) / "persistence"
    persistence.mkdir(parents=True, exist_ok=True)
    state = runtime.conversation_dir(conversation_id) / "base_state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}")
    os.utime(state, (age, age))
    return conversation_id, identity.workspace_path


@pytest.fixture
def usage(monkeypatch) -> Iterator[list[float]]:
    """Disk usage readings, one per check; the last one repeats."""
    readings: list[float] = []

    def read(_path: Path) -> float:
        return readings.pop(0) if len(readings) > 1 else readings[0]

    monkeypatch.setattr(registry_module, "_disk_usage", read)
    yield readings


@pytest.mark.asyncio
async def test_under_budget_sheds_nothing(tmp_path, monkeypatch, usage):
    runtime = registry(tmp_path, monkeypatch)
    _, workspace = provision(runtime, age=1)
    shed, _ = make_repo(workspace)
    usage.append(0.5)

    await runtime._enforce_disk_budget()

    assert all(path.is_dir() for path in shed)


@pytest.mark.asyncio
async def test_over_budget_sheds_oldest_stopped_runtime_first(
    tmp_path, monkeypatch, usage
):
    runtime = registry(tmp_path, monkeypatch)
    _, new_workspace = provision(runtime, age=2_000)
    _, old_workspace = provision(runtime, age=1_000)
    new_shed, _ = make_repo(new_workspace)
    old_shed, old_kept = make_repo(old_workspace)
    # Over budget, then back under once the oldest runtime is shed.
    usage.extend([0.95, 0.7])

    await runtime._enforce_disk_budget()

    assert not any(path.exists() for path in old_shed)
    assert all(path.is_file() for path in old_kept)
    assert all(path.is_dir() for path in new_shed)
    assert list(runtime.provisioning.data_root.glob("*/.cache-pruned-*")) == []


@pytest.mark.asyncio
async def test_live_runtime_keeps_its_files(tmp_path, monkeypatch, usage):
    runtime = registry(tmp_path, monkeypatch)
    conversation_id, workspace = provision(runtime, age=1)
    shed, _ = make_repo(workspace)
    runtime._containers[conversation_id] = ConversationContainer(
        host="http://127.0.0.1", api_key="k", container_id="c"
    )
    usage.append(0.95)

    await runtime._enforce_disk_budget()

    assert all(path.is_dir() for path in shed)


@pytest.mark.asyncio
async def test_caller_supplied_workspace_is_never_touched(tmp_path, monkeypatch, usage):
    runtime = registry(tmp_path, monkeypatch)
    _, workspace = provision(runtime, age=1, workspace=tmp_path / "user-checkout")
    shed, _ = make_repo(workspace)
    usage.append(0.95)

    await runtime._enforce_disk_budget()

    assert all(path.is_dir() for path in shed)


@pytest.mark.asyncio
async def test_nested_checkouts_are_shed_but_ignored_repos_kept(
    tmp_path, monkeypatch, usage
):
    runtime = registry(tmp_path, monkeypatch)
    _, workspace = provision(runtime, age=1)
    (workspace).mkdir(parents=True, exist_ok=True)
    nested_shed, nested_kept = make_repo(workspace / "project")
    # A workspace that ignores a cloned repo must not lose the clone.
    make_repo(workspace / "vendor-clone")
    git(workspace, "init", "-q")
    (workspace / ".gitignore").write_text("vendor-clone/\n")
    usage.append(0.95)

    await runtime._enforce_disk_budget()

    assert not any(path.exists() for path in nested_shed)
    assert all(path.is_file() for path in nested_kept)
    assert (workspace / "vendor-clone" / "main.py").is_file()


@pytest.mark.asyncio
async def test_repo_config_cannot_run_commands_on_the_host(
    tmp_path, monkeypatch, usage
):
    runtime = registry(tmp_path, monkeypatch)
    _, workspace = provision(runtime, age=1)
    make_repo(workspace)
    marker = tmp_path / "pwned"
    hook = tmp_path / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    git(workspace, "config", "core.fsmonitor", str(hook))
    usage.append(0.95)

    await runtime._enforce_disk_budget()

    assert not marker.exists()


@pytest.mark.asyncio
async def test_non_git_workspace_is_skipped(tmp_path, monkeypatch, usage):
    runtime = registry(tmp_path, monkeypatch)
    _, workspace = provision(runtime, age=1)
    deps = workspace / "node_modules" / "left-pad"
    deps.mkdir(parents=True)
    usage.append(0.95)

    await runtime._enforce_disk_budget()

    assert deps.is_dir()


@pytest.mark.asyncio
async def test_disk_budget_loop_runs_only_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_module, "_DISK_BUDGET_INTERVAL", 3600)
    runtime = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "cleanup_stale_containers", lambda: None)

    await runtime.start()
    assert runtime._disk_budget_task is not None
    await runtime.shutdown()
    assert runtime._disk_budget_task is None

    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(tmp_path / "persistence"))
    unset = DockerConversationRegistry(
        Config(
            conversations_path=tmp_path / "conversations",
            secret_key=SecretStr("outer-key"),
        )
    )
    monkeypatch.setattr(unset, "cleanup_stale_containers", lambda: None)
    await unset.start()
    assert unset._disk_budget_task is None
    await unset.shutdown()
