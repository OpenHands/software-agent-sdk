import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk.workspace import (
    BaseWorkspace,
    LandlockWorkspace,
    LocalWorkspace,
    Workspace,
    get_landlock_abi_version,
    is_landlock_supported,
)
from openhands.sdk.workspace.landlock import (
    _apply_landlock_and_group,
    get_handled_fs_access,
    get_read_only_fs_access,
    is_unshare_user_supported,
)


def test_workspace_factory_backend_selection(tmp_path: Path):
    """Workspace factory returns LandlockWorkspace when backend='landlock'."""
    ws_landlock = Workspace(working_dir=tmp_path, backend="landlock")
    assert isinstance(ws_landlock, LandlockWorkspace)
    assert ws_landlock.working_dir == str(tmp_path)

    # Default backend remains LocalWorkspace for backward compatibility
    ws_local = Workspace(working_dir=tmp_path)
    assert isinstance(ws_local, LocalWorkspace)
    assert ws_local.working_dir == str(tmp_path)


def test_workspace_factory_backend_auto(tmp_path: Path):
    """Workspace factory with backend='auto' selects Landlock when supported."""
    ws = Workspace(working_dir=tmp_path, backend="auto")
    if is_landlock_supported():
        assert isinstance(ws, LandlockWorkspace)
    else:
        assert isinstance(ws, LocalWorkspace)


def test_discriminated_union_model_roundtrip(tmp_path: Path):
    """LandlockWorkspace properly serializes and deserializes via BaseWorkspace."""
    ws = LandlockWorkspace(
        working_dir=str(tmp_path),
        enable_landlock=True,
        enable_process_group=True,
    )
    dumped = ws.model_dump()
    assert dumped["kind"] == "LandlockWorkspace"
    assert dumped["working_dir"] == str(tmp_path)

    # Validate back through BaseWorkspace discriminated union
    restored = BaseWorkspace.model_validate(dumped)
    assert isinstance(restored, LandlockWorkspace)
    assert restored.working_dir == str(tmp_path)
    assert restored.enable_landlock is True


def test_landlock_abi_masks():
    """Verify bitmasks for handled and read-only filesystem access across ABIs."""
    assert get_handled_fs_access(0) == 0
    assert get_handled_fs_access(1) == 0x1FFF
    assert get_handled_fs_access(2) == 0x3FFF
    assert get_handled_fs_access(3) == 0x7FFF
    assert get_handled_fs_access(4) == 0x7FFF
    assert get_handled_fs_access(5) == 0xFFFF
    assert get_handled_fs_access(6) == 0xFFFF

    ro_1 = get_read_only_fs_access(1)
    # ABI 1 includes execute (1), read_file (4), read_dir (8) = 13 (0x0D)
    assert ro_1 == ((1 << 0) | (1 << 2) | (1 << 3))

    ro_2 = get_read_only_fs_access(2)
    # ABI 2 adds refer (1 << 13)
    assert (ro_2 & (1 << 13)) != 0


def test_landlock_abi_probe_non_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Landlock detection gracefully reports version 0 on non-Linux platforms."""
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    assert get_landlock_abi_version() == 0
    assert not is_landlock_supported()

    ws = LandlockWorkspace(working_dir=tmp_path)
    assert ws.abi_version == 0
    assert not ws.is_landlock_supported
    assert not ws.is_enforcing
    assert ws.fallback_mode is True


def test_landlock_abi_probe_syscall_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Landlock detection gracefully handles syscall failure (ENOSYS/EOPNOTSUPP)."""
    mock_libc = MagicMock()
    mock_libc.syscall.return_value = -1

    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setattr("ctypes.CDLL", lambda *args, **kwargs: mock_libc)

    assert get_landlock_abi_version() == 0
    ws = LandlockWorkspace(working_dir=tmp_path)
    assert ws.abi_version == 0
    assert ws.fallback_mode is True


def test_execute_command_success(tmp_path: Path):
    """Execute simple bash commands and verify stdout, stderr, and exit codes."""
    ws = LandlockWorkspace(working_dir=tmp_path)
    res = ws.execute_command("echo 'hello from landlock'")

    assert res.exit_code == 0
    assert "hello from landlock" in res.stdout
    assert res.stderr == ""
    assert not res.timeout_occurred
    assert res.command == "echo 'hello from landlock'"


def test_execute_command_exit_code_and_stderr(tmp_path: Path):
    """Non-zero exit codes and stderr streams are correctly captured."""
    ws = LandlockWorkspace(working_dir=tmp_path)
    res = ws.execute_command(
        "sh -c 'echo \"warning notice\" >&2; exit 42'",
        timeout=10.0,
    )

    assert res.exit_code == 42
    assert "warning notice" in res.stderr
    assert not res.timeout_occurred


def test_execute_command_cwd_handling(tmp_path: Path):
    """Commands execute in specified subdirectory or default to working_dir."""
    sub_dir = tmp_path / "subproject"
    sub_dir.mkdir(parents=True, exist_ok=True)

    ws = LandlockWorkspace(working_dir=tmp_path)
    res = ws.execute_command("pwd", cwd="subproject")

    assert res.exit_code == 0
    assert str(sub_dir) in res.stdout.strip()


def test_execute_command_timeout_and_process_extinction(tmp_path: Path):
    """Commands that exceed timeout are terminated with process group extinction."""
    ws = LandlockWorkspace(working_dir=tmp_path, enable_process_group=True)

    # Spawn command with background child process
    res = ws.execute_command(
        "sh -c 'sleep 30'",
        timeout=0.5,
    )

    assert res.timeout_occurred is True
    assert res.exit_code == -1


def test_file_operations_upload_download(tmp_path: Path):
    """Test file_upload and file_download in LandlockWorkspace."""
    ws = LandlockWorkspace(working_dir=tmp_path)

    # Create source file
    src_file = tmp_path / "origin.txt"
    src_file.write_text("sample content for workspace test")

    dest_rel = "uploaded.txt"
    upload_res = ws.file_upload(src_file, dest_rel)

    assert upload_res.success is True
    assert upload_res.file_size == len("sample content for workspace test")

    uploaded_path = tmp_path / dest_rel
    assert uploaded_path.exists()
    assert uploaded_path.read_text() == "sample content for workspace test"

    # Test download
    dl_target = tmp_path / "downloaded.txt"
    dl_res = ws.file_download(dest_rel, dl_target)

    assert dl_res.success is True
    assert dl_target.exists()
    assert dl_target.read_text() == "sample content for workspace test"


def test_pause_and_resume_noop(tmp_path: Path):
    """pause and resume operate as safe no-ops."""
    ws = LandlockWorkspace(working_dir=tmp_path)
    ws.pause()
    ws.resume()


def test_sensitive_environment_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Sensitive credentials and API keys are redacted from subprocess environment."""
    monkeypatch.setenv("SESSION_API_KEY", "sensitive-key-12345")
    monkeypatch.setenv("OH_SECRET_KEY", "cipher-secret-67890")
    monkeypatch.setenv("OH_SESSION_API_KEYS_0", "rotation-key-abcde")

    ws = LandlockWorkspace(working_dir=tmp_path)
    cmd = (
        'python3 -c "import os; '
        "print('SESSION:', os.environ.get('SESSION_API_KEY')); "
        "print('SECRET:', os.environ.get('OH_SECRET_KEY')); "
        "print('AGENT:', os.environ.get('AI_AGENT'))\""
    )
    res = ws.execute_command(cmd)

    assert res.exit_code == 0
    assert "SESSION: None" in res.stdout
    assert "SECRET: None" in res.stdout
    assert "AGENT: openhands" in res.stdout


def test_context_exit_automation_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Leaving workspace context triggers automation callback when configured."""
    monkeypatch.setenv("AUTOMATION_CALLBACK_URL", "https://svc.test/complete")
    ws = LandlockWorkspace(working_dir=tmp_path)
    ws.register_cost(0.75)

    with patch("httpx.Client") as MockClient:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        MockClient.return_value = mock_client

        with ws:
            pass

        assert MockClient.call_count == 1
        payload = mock_client.post.call_args.kwargs["json"]
        assert payload["cost"] == 0.75
        assert payload["status"] == "COMPLETED"


@pytest.mark.skipif(
    not is_landlock_supported(),
    reason="Linux Landlock LSM is not supported on this host",
)
def test_landlock_filesystem_containment(tmp_path: Path):
    """Verify that Landlock blocks write access outside permitted workspace paths."""
    ws = LandlockWorkspace(
        working_dir=tmp_path,
        enable_landlock=True,
    )
    assert ws.is_enforcing is True

    # 1. Writing inside workspace working_dir should succeed
    inside_res = ws.execute_command("touch inside.txt && ls inside.txt")
    assert inside_res.exit_code == 0
    assert "inside.txt" in inside_res.stdout
    assert (tmp_path / "inside.txt").exists()

    # 2. Writing to an external unpermitted directory should be denied by Landlock LSM
    # Create an outside directory not in read_write_paths
    outside_dir = tmp_path.parent / "unauthorized_dir_landlock_test"
    outside_dir.mkdir(parents=True, exist_ok=True)
    try:
        forbidden_file = outside_dir / "forbidden.txt"
        block_res = ws.execute_command(f"touch '{forbidden_file}'")

        # Landlock returns EACCES (Permission denied) -> touch exit code 1
        assert block_res.exit_code != 0
        assert not forbidden_file.exists()
    finally:
        if outside_dir.exists():
            import shutil

            shutil.rmtree(outside_dir, ignore_errors=True)


def test_workspace_factory_invalid_backend_rejected(tmp_path: Path):
    """Workspace factory rejects unknown backend values with ValueError."""
    with pytest.raises(
        ValueError, match="Unknown workspace backend: 'invalid_backend'"
    ):
        Workspace(working_dir=tmp_path, backend="invalid_backend")

    with pytest.raises(ValueError, match="Unknown workspace backend: 'docker'"):
        Workspace(working_dir=tmp_path, backend="docker")


def test_relative_working_dir_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Relative working_dir and allowlist paths resolved to absolute paths."""
    monkeypatch.chdir(tmp_path)
    rel_path = "subproject/workspace"
    ws = LandlockWorkspace(
        working_dir=rel_path,
        read_only_paths=["extra_ro"],
        read_write_paths=["extra_rw"],
    )

    expected_wd = str((tmp_path / rel_path).resolve())
    expected_ro = str((tmp_path / "extra_ro").resolve())
    expected_rw = str((tmp_path / "extra_rw").resolve())

    assert ws.working_dir == expected_wd
    assert expected_ro in ws.read_only_paths
    assert expected_rw in ws.read_write_paths

    res = ws.execute_command("pwd")
    assert res.exit_code == 0
    assert res.stdout.strip() == expected_wd


def test_execute_command_cwd_validation_outside_workspace(tmp_path: Path):
    """cwd pointing outside workspace root raises ValueError."""
    ws = LandlockWorkspace(working_dir=tmp_path)
    outside_dir = tmp_path.parent / "unauthorized_outside_dir"

    with pytest.raises(ValueError, match="must be inside workspace root"):
        ws.execute_command("pwd", cwd=outside_dir)

    with pytest.raises(ValueError, match="must be inside workspace root"):
        ws.execute_command("pwd", cwd="../outside")

    # Valid relative subdirectories succeed
    sub = tmp_path / "subdir"
    sub.mkdir(parents=True, exist_ok=True)
    res = ws.execute_command("pwd", cwd="subdir")
    assert res.exit_code == 0
    assert res.stdout.strip() == str(sub.resolve())


def test_execute_command_setsid_timeout_bounded(tmp_path: Path):
    """Commands spawning detached setsid processes complete within bounded timeout."""
    ws = LandlockWorkspace(working_dir=tmp_path, enable_process_group=True)

    start = time.monotonic()
    # setsid grandchild holding pipes would previously hang execute_command
    res = ws.execute_command("setsid sleep 15 & sleep 15", timeout=0.5)
    elapsed = time.monotonic() - start

    assert res.timeout_occurred is True
    assert res.exit_code == -1
    # Must terminate promptly (bounded within ~1.5s, far below 15s)
    assert elapsed < 2.0


@pytest.mark.skipif(
    not is_landlock_supported(),
    reason="Linux Landlock LSM is not supported on this host",
)
def test_landlock_dev_null_redirection(tmp_path: Path):
    """Verify shell redirections to /dev/null, /dev/zero, and /dev/urandom succeed."""
    ws = LandlockWorkspace(
        working_dir=tmp_path,
        enable_landlock=True,
    )
    assert ws.is_enforcing is True

    # 1. /dev/null write redirection
    res_null = ws.execute_command("echo 'suppressed output' > /dev/null")
    assert res_null.exit_code == 0
    assert res_null.stderr == ""

    # 2. /dev/zero reading and writing to workspace
    res_zero = ws.execute_command("head -c 32 /dev/zero > zeros.dat")
    assert res_zero.exit_code == 0
    assert (tmp_path / "zeros.dat").stat().st_size == 32

    # 3. /dev/urandom reading
    res_random = ws.execute_command("head -c 16 /dev/urandom > rand.dat")
    assert res_random.exit_code == 0
    assert (tmp_path / "rand.dat").stat().st_size == 16


def test_is_unshare_user_supported_probing(monkeypatch: pytest.MonkeyPatch):
    """is_unshare_user_supported identifies unprivileged userns availability."""
    assert isinstance(is_unshare_user_supported(), bool)

    # When unshare binary is missing
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert is_unshare_user_supported() is False

    # When unshare -Ur true fails
    mock_run = MagicMock()
    mock_run.returncode = 1
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/unshare")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: mock_run)
    assert is_unshare_user_supported() is False


def test_landlock_restrict_self_failure_raises_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Failure in landlock_restrict_self raises RuntimeError (fail-closed)."""
    call_count = 0

    def mock_syscall(nr, *args):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # create_ruleset returns a valid fd
            return 99
        if nr == 446:  # SYS_landlock_restrict_self
            return -1
        return 0

    mock_libc = MagicMock()
    mock_libc.syscall.side_effect = mock_syscall
    mock_libc.prctl.return_value = 0

    monkeypatch.setattr("ctypes.CDLL", lambda *args, **kwargs: mock_libc)
    monkeypatch.setattr("ctypes.get_errno", lambda: 1)  # EPERM

    with pytest.raises(
        RuntimeError, match="landlock_restrict_self failed with errno 1"
    ):
        _apply_landlock_and_group(
            read_only_paths=[],
            read_write_paths=[str(tmp_path)],
            abi_version=1,
            enable_process_group=False,
        )


def test_landlock_workspace_isinstance_local_workspace(tmp_path: Path):
    """LandlockWorkspace inherits from LocalWorkspace for seamless Conversation integration."""
    ws = LandlockWorkspace(working_dir=tmp_path)
    assert isinstance(ws, LocalWorkspace)
    assert isinstance(ws, BaseWorkspace)

    ws_factory = Workspace(working_dir=tmp_path, backend="landlock")
    assert isinstance(ws_factory, LocalWorkspace)
    assert isinstance(ws_factory, LandlockWorkspace)


def test_conversation_accepts_landlock_workspace(tmp_path: Path):
    """Conversation factory and LocalConversation accept LandlockWorkspace without assertion error."""
    from openhands.sdk.agent import Agent
    from openhands.sdk.conversation import Conversation
    from openhands.sdk.llm import LLM
    from pydantic import SecretStr

    llm = LLM(model="gpt-5.5", api_key=SecretStr("mock-key"))
    agent = Agent(llm=llm, tools=[])
    ws = Workspace(working_dir=tmp_path, backend="landlock")

    conv = Conversation(agent=agent, workspace=ws)
    assert conv.workspace is ws
    assert isinstance(conv.workspace, LocalWorkspace)

