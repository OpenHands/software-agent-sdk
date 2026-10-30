"""Test ApptainerWorkspace import and GPU passthrough behavior."""

import gc
import subprocess
import sys
from unittest.mock import MagicMock, Mock, patch

import pytest


@pytest.fixture
def mock_apptainer_workspace(tmp_path):
    """Fixture to create a mocked ApptainerWorkspace with minimal setup."""
    from openhands.workspace import ApptainerWorkspace

    sif_path = tmp_path / "test.sif"
    sif_path.write_text("fake sif")

    with (
        patch("openhands.workspace.apptainer.workspace.execute_command") as mock_exec,
        patch(
            "openhands.workspace.apptainer.workspace.check_port_available",
            return_value=True,
        ),
    ):
        mock_exec.return_value = Mock(returncode=0, stdout="", stderr="")

        def _create_workspace(
            *, enable_gpu: bool = False, extra_bind_mounts: list[str] | None = None
        ):
            with (
                patch.object(ApptainerWorkspace, "_start_container"),
                patch.object(ApptainerWorkspace, "_wait_for_health"),
            ):
                workspace = ApptainerWorkspace(
                    sif_file=str(sif_path),
                    host_port=8000,
                    detach_logs=False,
                    enable_gpu=enable_gpu,
                    extra_bind_mounts=extra_bind_mounts or [],
                )

            return workspace, mock_exec

        yield _create_workspace


def test_apptainer_workspace_import():
    """Test that ApptainerWorkspace can be imported from the package."""
    from openhands.workspace import ApptainerWorkspace

    assert ApptainerWorkspace is not None
    assert hasattr(ApptainerWorkspace, "__init__")


def test_apptainer_workspace_inheritance():
    """Test that ApptainerWorkspace inherits from RemoteWorkspace."""
    from openhands.sdk.workspace import RemoteWorkspace
    from openhands.workspace import ApptainerWorkspace

    assert issubclass(ApptainerWorkspace, RemoteWorkspace)


def test_apptainer_workspace_has_gpu_field():
    """Test that ApptainerWorkspace exposes the GPU passthrough option."""
    from openhands.workspace import ApptainerWorkspace

    assert "enable_gpu" in ApptainerWorkspace.model_fields


@pytest.mark.parametrize("enable_gpu", [False, True])
def test_apptainer_workspace_gpu_passthrough_flag(
    mock_apptainer_workspace, enable_gpu: bool
):
    """Test that GPU passthrough toggles the Apptainer --nv flag."""
    workspace, _ = mock_apptainer_workspace(enable_gpu=enable_gpu)

    fake_process = Mock(stdout=None)
    with patch(
        "openhands.workspace.apptainer.workspace.subprocess.Popen",
        return_value=fake_process,
    ) as mock_popen:
        workspace._start_container()

    run_cmd = mock_popen.call_args.args[0]

    assert run_cmd[:2] == ["apptainer", "run"]
    assert ("--nv" in run_cmd) is enable_gpu
    assert workspace._sif_path in run_cmd

    workspace._process = None
    workspace._instance_name = None


def test_apptainer_workspace_extra_bind_mounts(mock_apptainer_workspace, monkeypatch):
    """Test that explicit and environment-provided bind mounts reach Apptainer."""
    monkeypatch.setenv("OPENHANDS_APPTAINER_EXTRA_BINDS", "/env/src:/env/dst:ro")
    workspace, _ = mock_apptainer_workspace(
        extra_bind_mounts=["/host/tokenizer:/host/tokenizer:ro"]
    )

    fake_process = Mock(stdout=None)
    with patch(
        "openhands.workspace.apptainer.workspace.subprocess.Popen",
        return_value=fake_process,
    ) as mock_popen:
        workspace._start_container()

    run_cmd = mock_popen.call_args.args[0]

    assert "--bind" in run_cmd
    assert "/host/tokenizer:/host/tokenizer:ro" in run_cmd
    assert "/env/src:/env/dst:ro" in run_cmd

    workspace._process = None
    workspace._instance_name = None


# ===========================================================================
# Startup failure paths
# ===========================================================================


@pytest.fixture
def apptainer_env(monkeypatch, tmp_path):
    """Fake the apptainer CLI and port probing; return the execute_command mock."""
    mock_exec = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.execute_command", mock_exec
    )
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.check_port_available", lambda p: True
    )
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.Path.home", lambda: tmp_path
    )
    return mock_exec


@pytest.fixture
def child_popen(monkeypatch):
    """Run a real Python child in place of `apptainer run`; collect the procs."""
    real_popen = subprocess.Popen
    procs: list[subprocess.Popen] = []
    script = {"code": "pass"}

    def fake_popen(cmd, **kwargs):
        proc = real_popen([sys.executable, "-I", "-S", "-c", script["code"]], **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.subprocess.Popen", fake_popen
    )
    yield script, procs
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()


@pytest.mark.parametrize(
    "sources",
    [{}, {"server_image": "img:latest", "sif_file": "/tmp/x.sif"}],
)
def test_requires_exactly_one_image_source(apptainer_env, sources):
    from openhands.workspace import ApptainerWorkspace

    with pytest.raises(ValueError, match="Exactly one"):
        ApptainerWorkspace(**sources)

    apptainer_env.assert_not_called()


def test_missing_sif_file_fails_before_starting(apptainer_env, tmp_path):
    from openhands.workspace import ApptainerWorkspace

    with pytest.raises(RuntimeError, match="SIF file not found"):
        ApptainerWorkspace(sif_file=str(tmp_path / "missing.sif"), host_port=8000)


def test_apptainer_unavailable_fails_with_install_hint(apptainer_env, tmp_path):
    from openhands.workspace import ApptainerWorkspace

    apptainer_env.return_value = Mock(returncode=127, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="Apptainer is not available"):
        ApptainerWorkspace(server_image="img:latest", host_port=8000)


def test_image_pull_failure_surfaces_stderr(apptainer_env):
    from openhands.workspace import ApptainerWorkspace

    def fake_exec(cmd, *args, **kwargs):
        if cmd[1] == "pull":
            return Mock(returncode=255, stdout="", stderr="manifest unknown")
        return Mock(returncode=0, stdout="", stderr="")

    apptainer_env.side_effect = fake_exec

    with pytest.raises(RuntimeError, match="manifest unknown"):
        ApptainerWorkspace(server_image="ghcr.io/x/img:tag", host_port=8000)


def test_wait_for_health_reports_exit_code_of_dead_process(
    mock_apptainer_workspace, monkeypatch
):
    workspace, _ = mock_apptainer_workspace()
    monkeypatch.setattr(
        "openhands.workspace.apptainer.workspace.urlopen",
        Mock(side_effect=OSError("connection refused")),
    )
    workspace._process = Mock(poll=Mock(return_value=3), returncode=3)

    with pytest.raises(RuntimeError, match="exit code 3"):
        workspace._wait_for_health(timeout=60)

    workspace._process = None
    workspace._instance_name = None


@pytest.mark.xfail(
    strict=True,
    reason="BUG: ApptainerWorkspace leaves the `apptainer run` process running "
    "when the health check fails during construction",
)
def test_failed_health_check_terminates_server_process(
    apptainer_env, child_popen, tmp_path
):
    from openhands.workspace import ApptainerWorkspace

    script, procs = child_popen
    script["code"] = "import time; time.sleep(30)"
    sif = tmp_path / "img.sif"
    sif.write_text("fake sif")

    with (
        patch.object(
            ApptainerWorkspace,
            "_wait_for_health",
            side_effect=RuntimeError("Container failed to become healthy in time"),
        ),
        pytest.raises(RuntimeError, match="healthy") as excinfo,
    ):
        ApptainerWorkspace(sif_file=str(sif), host_port=8000, detach_logs=False)

    # excinfo keeps the half-built workspace alive, so __del__ has not run yet.
    alive_after_failure = procs[0].poll() is None
    del excinfo
    gc.collect()
    assert not alive_after_failure


@pytest.mark.xfail(
    strict=True,
    reason="BUG: with detach_logs=False the server's stdout PIPE is never read, "
    "so it blocks once ~64 KiB of logs fill the pipe buffer",
)
def test_undrained_logs_do_not_block_server(mock_apptainer_workspace, child_popen):
    script, procs = child_popen
    script["code"] = "import sys; sys.stdout.write('x' * 1_000_000); sys.stdout.flush()"
    workspace, _ = mock_apptainer_workspace()  # detach_logs=False

    workspace._start_container()

    try:
        procs[0].wait(timeout=2)
    except subprocess.TimeoutExpired:
        pytest.fail("agent-server child blocked writing its logs")
    finally:
        workspace._process = None
        workspace._instance_name = None


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"SESSION_API_KEY": "legacy-key"}, "legacy-key"),
        pytest.param(
            {"OH_SESSION_API_KEYS_0": "current-key"},
            "current-key",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: ApptainerWorkspace ignores the forwarded "
                "OH_SESSION_API_KEYS_0, which the agent server prefers",
            ),
        ),
        pytest.param(
            {"SESSION_API_KEY": "legacy-key", "OH_SESSION_API_KEYS_0": "current-key"},
            "current-key",
            marks=pytest.mark.xfail(
                strict=True,
                reason="BUG: ApptainerWorkspace sends SESSION_API_KEY although the "
                "agent server prefers the forwarded OH_SESSION_API_KEYS_0",
            ),
        ),
    ],
)
def test_client_key_matches_key_server_accepts(
    mock_apptainer_workspace, monkeypatch, env, expected
):
    """Both key variables are forwarded; the server resolves the V1 one first."""
    monkeypatch.delenv("SESSION_API_KEY", raising=False)
    monkeypatch.delenv("OH_SESSION_API_KEYS_0", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    workspace, _ = mock_apptainer_workspace()

    assert workspace.api_key == expected


@pytest.mark.xfail(
    strict=True,
    reason="BUG: ApptainerWorkspace.__exit__ skips RemoteWorkspace.__exit__, so "
    "the AUTOMATION_CALLBACK_URL completion callback is never sent",
)
def test_exit_sends_completion_callback(mock_apptainer_workspace, monkeypatch):
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    workspace, _ = mock_apptainer_workspace()

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        workspace.__exit__(None, None, None)

        mock_client.post.assert_called_once()
        assert mock_client.post.call_args.args[0] == "https://svc.test/complete"
