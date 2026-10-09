"""Test PodmanWorkspace import, launch command, and startup rollback."""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace import PodmanWorkspace


MODULE = "openhands.workspace.podman.workspace"


def _flag_values(cmd: list[str], flag: str) -> list[str]:
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == flag]


@pytest.fixture
def fake_podman():
    """Replace podman, the port check, and the health endpoint with fakes."""
    fake = SimpleNamespace(
        calls=[],
        run_result=None,
        run_env=None,
        healthy=True,
        running="true",
        logs="",
    )

    def execute(cmd, **kwargs):
        fake.calls.append(cmd)
        if cmd[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, f"{fake.running}\n", "")
        if cmd[1] == "logs":
            return subprocess.CompletedProcess(cmd, 0, fake.logs, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def run(cmd, **kwargs):
        fake.calls.append(cmd)
        fake.run_env = kwargs["env"]
        return fake.run_result or subprocess.CompletedProcess(cmd, 0, "id\n", "")

    def urlopen(url, **kwargs):
        if not fake.healthy:
            raise URLError("connection refused")
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        return response

    with (
        patch(f"{MODULE}.execute_command", side_effect=execute),
        patch(f"{MODULE}.subprocess.run", side_effect=run),
        patch(f"{MODULE}.urlopen", side_effect=urlopen),
        patch(f"{MODULE}.check_port_available", return_value=True),
        patch(f"{MODULE}.shutil.which", return_value="/usr/bin/podman"),
        patch(f"{MODULE}.time.sleep"),
    ):
        yield fake


def _run_command(fake) -> list[str]:
    return next(cmd for cmd in fake.calls if cmd[1] == "run")


def _container_name(fake) -> str:
    return _flag_values(_run_command(fake), "--name")[0]


def test_podman_workspace_import():
    """PodmanWorkspace is exported and is a RemoteWorkspace."""
    import openhands.workspace

    assert "PodmanWorkspace" in openhands.workspace.__all__
    assert issubclass(PodmanWorkspace, RemoteWorkspace)


def test_podman_workspace_launch_command(fake_podman, monkeypatch):
    """The container publishes on loopback and receives secrets by name only."""
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "secret-key")
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    workspace = PodmanWorkspace(
        host_port=8000,
        volumes=["/src:/workspace/project:Z"],
        detach_logs=False,
    )

    cmd = _run_command(fake_podman)
    assert cmd[:3] == ["podman", "run", "--detach"]
    assert _flag_values(cmd, "--publish") == ["127.0.0.1:8000:8000"]
    assert _flag_values(cmd, "--volume") == ["/src:/workspace/project:Z"]
    assert _flag_values(cmd, "--userns") == ["keep-id:uid=10001,gid=10001"]
    assert _flag_values(cmd, "--env") == ["OH_SESSION_API_KEYS_0"]
    assert cmd[-5:] == [
        "ghcr.io/openhands/agent-server:latest-python",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    assert not any("secret-key" in arg for arg in cmd)
    assert fake_podman.run_env["OH_SESSION_API_KEYS_0"] == "secret-key"
    assert workspace.host == "http://127.0.0.1:8000"
    assert workspace.api_key == "secret-key"

    workspace.cleanup()

    assert fake_podman.calls[-1] == [
        "podman",
        "rm",
        "--force",
        "--ignore",
        _container_name(fake_podman),
    ]


def test_podman_workspace_removes_container_when_run_fails(fake_podman):
    """A failed 'podman run' removes the container it may have created."""
    fake_podman.run_result = subprocess.CompletedProcess(
        [], 126, "", "Error: pasta failed with exit code 1"
    )

    # Holding the exception keeps the workspace alive, so __del__ cannot be
    # what removes the container.
    with pytest.raises(RuntimeError) as excinfo:
        PodmanWorkspace(host_port=8000, detach_logs=False)

    name = _container_name(fake_podman)
    assert ["podman", "rm", "--force", "--ignore", name] in fake_podman.calls
    assert "pasta failed" in str(excinfo.value)


def test_podman_workspace_reports_logs_when_server_exits(fake_podman):
    """A container that exits during startup is removed and its logs reported."""
    fake_podman.healthy = False
    fake_podman.running = "false"
    fake_podman.logs = "error: unrecognized arguments\n"

    with pytest.raises(RuntimeError) as excinfo:
        PodmanWorkspace(host_port=8000, detach_logs=False)

    name = _container_name(fake_podman)
    assert fake_podman.calls[-1] == ["podman", "rm", "--force", "--ignore", name]
    assert "stopped unexpectedly" in str(excinfo.value)
    assert "unrecognized arguments" in str(excinfo.value)


def test_podman_workspace_requires_podman(fake_podman):
    """A missing podman binary fails before any podman command runs."""
    with (
        patch(f"{MODULE}.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="Podman is not installed"),
    ):
        PodmanWorkspace(host_port=8000)

    assert fake_podman.calls == []
