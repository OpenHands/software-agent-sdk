"""Test PodmanWorkspace import, launch command, and startup rollback."""

import gc
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from openhands.sdk.workspace import RemoteWorkspace
from openhands.workspace import DockerWorkspace, PodmanWorkspace


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
        rm_results=[],
        image_present=True,
    )

    def execute(cmd, **kwargs):
        fake.calls.append(cmd)
        if cmd[1:3] == ["image", "exists"]:
            return subprocess.CompletedProcess(cmd, int(not fake.image_present), "", "")
        if cmd[1:3] == ["container", "inspect"]:
            return subprocess.CompletedProcess(cmd, 0, f"{fake.running}\n", "")
        if cmd[1] == "logs":
            return subprocess.CompletedProcess(cmd, 0, fake.logs, "")
        if cmd[1] == "rm" and fake.rm_results:
            return fake.rm_results.pop(0)
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


def test_podman_workspace_launch_command(fake_podman, monkeypatch, tmp_path):
    """The container publishes on loopback and receives secrets by name only."""
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "secret-key")
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)

    workspace = PodmanWorkspace(
        host_port=8000,
        volumes=[f"{tmp_path}:/workspace/project:Z"],
        detach_logs=False,
    )

    cmd = _run_command(fake_podman)
    assert cmd[:3] == ["podman", "run", "--detach"]
    assert _flag_values(cmd, "--publish") == ["127.0.0.1:8000:8000"]
    assert _flag_values(cmd, "--platform") == ["linux/amd64"]
    assert "--device" not in cmd
    assert _flag_values(cmd, "--volume") == [f"{tmp_path}:/workspace/project:Z"]
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
    """A missing podman binary fails before any port check or podman command."""
    with (
        patch(f"{MODULE}.shutil.which", return_value=None),
        patch(f"{MODULE}.check_port_available") as port_check,
        pytest.raises(RuntimeError, match="Podman is not installed"),
    ):
        PodmanWorkspace(host_port=8000)

    port_check.assert_not_called()
    assert fake_podman.calls == []


def test_podman_workspace_no_build_import():
    """Importing openhands.workspace does not pull in build-time dependencies."""
    code = (
        "import sys\n"
        "from openhands.workspace import PodmanWorkspace\n"
        "print('1' if 'openhands.agent_server.docker.build' in sys.modules else '0')\n"
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": ""},
        cwd=root,
    )
    assert result.stdout.strip() == "0"


def test_podman_workspace_shares_docker_fields():
    """Fields shared with DockerWorkspace keep their names."""
    shared = [
        "server_image",
        "volumes",
        "forward_env",
        "host_port",
        "working_dir",
        "detach_logs",
        "platform",
        "extra_ports",
        "enable_gpu",
        "health_check_timeout",
    ]
    assert set(shared) <= PodmanWorkspace.model_fields.keys()
    assert set(shared) <= DockerWorkspace.model_fields.keys()


def test_podman_workspace_optional_flags(fake_podman):
    """platform, extra_ports, and enable_gpu reach the pull and run commands."""
    fake_podman.image_present = False

    workspace = PodmanWorkspace(
        host_port=8000,
        platform="linux/arm64",
        extra_ports=True,
        enable_gpu=True,
        detach_logs=False,
    )

    image = "ghcr.io/openhands/agent-server:latest-python"
    assert ["podman", "pull", "--platform", "linux/arm64", image] in fake_podman.calls
    cmd = _run_command(fake_podman)
    assert _flag_values(cmd, "--platform") == ["linux/arm64"]
    assert _flag_values(cmd, "--publish") == [
        "127.0.0.1:8000:8000",
        "127.0.0.1:8001:8001",
    ]
    assert _flag_values(cmd, "--device") == ["nvidia.com/gpu=all"]
    workspace.cleanup()


def test_podman_workspace_vscode_port_must_be_free(fake_podman):
    """With extra_ports, a taken VSCode port fails before podman runs."""
    with (
        patch(f"{MODULE}.check_port_available", side_effect=lambda p: p != 8001),
        pytest.raises(RuntimeError, match="Port 8001 is not available for VSCode"),
    ):
        PodmanWorkspace(host_port=8000, extra_ports=True)

    assert fake_podman.calls == []


def test_podman_workspace_rejects_missing_volume_source(fake_podman, tmp_path):
    """A host path that does not exist is named in the error."""
    missing = tmp_path / "missing"

    with pytest.raises(ValueError, match=f"does not exist: {missing}"):
        PodmanWorkspace(volumes=[f"{missing}:/workspace/project"])

    assert fake_podman.calls == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any directory")
def test_podman_workspace_rejects_unreadable_volume_source(fake_podman, tmp_path):
    """A host path the user cannot read is named in the error."""
    locked = tmp_path / "locked"
    locked.mkdir(mode=0)
    try:
        with pytest.raises(ValueError, match=f"not readable: {locked}"):
            PodmanWorkspace(volumes=[f"{locked}:/workspace/project:Z"])
    finally:
        locked.chmod(0o700)

    assert fake_podman.calls == []


def test_podman_workspace_accepts_named_volume(fake_podman):
    """A source that is not a path is a named volume and is passed through."""
    workspace = PodmanWorkspace(
        host_port=8000, volumes=["cache:/home/openhands/.cache"], detach_logs=False
    )

    cmd = _run_command(fake_podman)
    assert _flag_values(cmd, "--volume") == ["cache:/home/openhands/.cache"]
    workspace.cleanup()


def _rm_calls(fake) -> list[list[str]]:
    return [cmd for cmd in fake.calls if cmd[1] == "rm"]


def test_podman_workspace_cleanup_retries_failed_removal(fake_podman):
    """A failed 'podman rm' keeps the container tracked for the next cleanup."""
    workspace = PodmanWorkspace(host_port=8000, detach_logs=False)
    fake_podman.rm_results = [
        subprocess.CompletedProcess([], 125, "", "Error: container is busy")
    ]

    with pytest.raises(RuntimeError, match="container is busy"):
        workspace.cleanup()
    workspace.cleanup()
    workspace.cleanup()

    name = _container_name(fake_podman)
    assert _rm_calls(fake_podman) == [["podman", "rm", "--force", "--ignore", name]] * 2


def test_podman_workspace_startup_error_survives_failed_removal(fake_podman):
    """A failed rollback is reported as a note on the startup error."""
    fake_podman.healthy = False
    fake_podman.running = "false"
    fake_podman.logs = "error: unrecognized arguments\n"
    fake_podman.rm_results = [
        subprocess.CompletedProcess([], 125, "", "Error: container is busy")
    ]

    with pytest.raises(RuntimeError, match="stopped unexpectedly") as excinfo:
        PodmanWorkspace(host_port=8000, detach_logs=False)

    assert any("container is busy" in note for note in excinfo.value.__notes__)

    # Releasing the exception frees the workspace by reference counting alone,
    # so __del__ retries the removal while this test's fake podman is active.
    gc.disable()
    try:
        del excinfo
    finally:
        gc.enable()
    name = _container_name(fake_podman)
    assert _rm_calls(fake_podman) == [["podman", "rm", "--force", "--ignore", name]] * 2


def test_podman_workspace_exit_keeps_body_error(fake_podman):
    """Leaving the context with an error keeps that error if removal fails."""
    fake_podman.rm_results = [
        subprocess.CompletedProcess([], 125, "", "Error: container is busy")
    ]

    with pytest.raises(ValueError, match="from the body") as excinfo:
        with PodmanWorkspace(host_port=8000, detach_logs=False):
            raise ValueError("from the body")

    assert any("container is busy" in note for note in excinfo.value.__notes__)
