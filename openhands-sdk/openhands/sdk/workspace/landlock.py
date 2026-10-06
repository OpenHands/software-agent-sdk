import ctypes
import os
import platform
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, Final

from pydantic import Field, PrivateAttr

from openhands.sdk.git.git_changes import get_git_changes
from openhands.sdk.git.git_diff import get_git_diff
from openhands.sdk.git.models import GitChange, GitDiff
from openhands.sdk.logger import get_logger
from openhands.sdk.utils.command import sanitized_env
from openhands.sdk.utils.redact import redact_text_secrets
from openhands.sdk.workspace.local import LocalWorkspace
from openhands.sdk.workspace.models import CommandResult, FileOperationResult


logger = get_logger(__name__)

# Landlock LSM constants and system call numbers
LANDLOCK_CREATE_RULESET_VERSION: Final[int] = 1 << 0
LANDLOCK_RULE_PATH_BENEATH: Final[int] = 1
PR_SET_NO_NEW_PRIVS: Final[int] = 38

# ABI 1 filesystem access flags
LANDLOCK_ACCESS_FS_EXECUTE: Final[int] = 1 << 0
LANDLOCK_ACCESS_FS_WRITE_FILE: Final[int] = 1 << 1
LANDLOCK_ACCESS_FS_READ_FILE: Final[int] = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR: Final[int] = 1 << 3
LANDLOCK_ACCESS_FS_REMOVE_DIR: Final[int] = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE: Final[int] = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR: Final[int] = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR: Final[int] = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG: Final[int] = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK: Final[int] = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO: Final[int] = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK: Final[int] = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM: Final[int] = 1 << 12

# ABI 2 additions (Linux 5.19+)
LANDLOCK_ACCESS_FS_REFER: Final[int] = 1 << 13

# ABI 3 additions (Linux 6.2+)
LANDLOCK_ACCESS_FS_TRUNCATE: Final[int] = 1 << 14

# ABI 5 additions (Linux 6.10+)
LANDLOCK_ACCESS_FS_IOCTL_DEV: Final[int] = 1 << 15

# Standard system read-only directories required for executing CLI binaries
DEFAULT_READ_ONLY_PATHS: tuple[str, ...] = (
    "/usr",
    "/lib",
    "/lib64",
    "/bin",
    "/sbin",
    "/etc",
    "/dev",
    "/proc",
    "/sys",
)

# Standard pseudo-devices required for redirection and terminal/random access
DEFAULT_READ_WRITE_DEVICES: tuple[str, ...] = (
    "/dev/null",
    "/dev/zero",
    "/dev/full",
    "/dev/random",
    "/dev/urandom",
    "/dev/tty",
)


class LandlockRulesetAttr(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        ("handled_access_fs", ctypes.c_uint64),
    ]


class LandlockPathBeneathAttr(ctypes.Structure):
    _fields_: ClassVar[Any] = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _get_syscall_numbers() -> tuple[int, int, int] | None:
    """Return the architecture-specific syscall numbers for Landlock.

    Returns:
        (SYS_landlock_create_ruleset, SYS_landlock_add_rule, SYS_landlock_restrict_self)
        or None if not supported.
    """
    if platform.system() != "Linux":
        return None
    # Standard numbers for x86_64, aarch64, riscv64, armv7l, armv8l
    return (444, 445, 446)


def get_landlock_abi_version() -> int:
    """Detect the highest supported Linux Landlock LSM ABI version (1-6+).

    Returns:
        The integer ABI version (e.g., 1 to 6+) if Landlock is active and supported,
        or 0 if unavailable, disabled, or not running on Linux.
    """
    if platform.system() != "Linux":
        return 0

    syscall_nums = _get_syscall_numbers()
    if syscall_nums is None:
        return 0

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if not hasattr(libc, "syscall"):
            return 0
        create_ruleset_nr = syscall_nums[0]
        # Querying with LANDLOCK_CREATE_RULESET_VERSION returns the supported ABI
        res = libc.syscall(create_ruleset_nr, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
        if res > 0:
            return int(res)
        return 0
    except Exception as exc:
        logger.debug(f"Landlock ABI probe failed: {exc}")
        return 0


def is_landlock_supported() -> bool:
    """Return True if Landlock LSM is supported on this host kernel."""
    return get_landlock_abi_version() >= 1


def get_handled_fs_access(abi: int) -> int:
    """Calculate handled filesystem access bitmask for the given Landlock ABI."""
    if abi <= 0:
        return 0
    if abi == 1:
        return 0x1FFF
    if abi == 2:
        return 0x3FFF
    if abi in (3, 4):
        return 0x7FFF
    # ABI >= 5
    return 0xFFFF


def get_read_only_fs_access(abi: int) -> int:
    """Calculate the read-only filesystem access bitmask for system paths."""
    base = (
        LANDLOCK_ACCESS_FS_EXECUTE
        | LANDLOCK_ACCESS_FS_READ_FILE
        | LANDLOCK_ACCESS_FS_READ_DIR
    )
    if abi >= 2:
        base |= LANDLOCK_ACCESS_FS_REFER
    return base


def get_file_fs_access(abi: int, write: bool = False) -> int:
    """Calculate the filesystem access bitmask for non-directory files."""
    access = LANDLOCK_ACCESS_FS_READ_FILE
    if write:
        access |= LANDLOCK_ACCESS_FS_WRITE_FILE
        if abi >= 3:
            access |= LANDLOCK_ACCESS_FS_TRUNCATE
    else:
        access |= LANDLOCK_ACCESS_FS_EXECUTE
    if abi >= 5:
        access |= LANDLOCK_ACCESS_FS_IOCTL_DEV
    return access


def is_unshare_user_supported() -> bool:
    """Check if unprivileged user namespace is supported by running unshare -Ur true."""
    if platform.system() != "Linux" or shutil.which("unshare") is None:
        return False
    try:
        res = subprocess.run(
            ["unshare", "-Ur", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


def _apply_landlock_and_group(
    read_only_paths: list[str],
    read_write_paths: list[str],
    abi_version: int,
    enable_process_group: bool = True,
) -> None:
    """Configure process group and Landlock LSM sandbox inside child preexec_fn."""
    # 1. Process group isolation for clean tree extinction on timeout
    if enable_process_group:
        try:
            os.setpgrp()
        except Exception:
            pass

    # 2. Landlock LSM sandbox restriction
    if abi_version <= 0:
        return

    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "syscall") or not hasattr(libc, "prctl"):
        raise RuntimeError("libc syscall or prctl not available")

    syscall_nums = _get_syscall_numbers()
    if not syscall_nums:
        raise RuntimeError(
            "Landlock syscall numbers not available on this architecture"
        )
    create_nr, add_nr, restrict_nr = syscall_nums

    # Enable PR_SET_NO_NEW_PRIVS (mandatory before landlock_restrict_self)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise RuntimeError(f"prctl(PR_SET_NO_NEW_PRIVS) failed with errno {errno}")

    handled_fs = get_handled_fs_access(abi_version)
    ro_fs = get_read_only_fs_access(abi_version)

    attr = LandlockRulesetAttr()
    attr.handled_access_fs = handled_fs

    rfd = libc.syscall(create_nr, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if rfd < 0:
        errno = ctypes.get_errno()
        raise RuntimeError(f"landlock_create_ruleset failed with errno {errno}")

    o_path = getattr(os, "O_PATH", 0o10000000)
    o_cloexec = getattr(os, "O_CLOEXEC", 0o2000000)

    try:
        # Apply read-only rules for system and dependency paths
        for path_str in read_only_paths:
            try:
                fd = os.open(path_str, o_path | o_cloexec)
            except OSError as err:
                raise RuntimeError(
                    f"Failed to open read-only path '{path_str}' "
                    f"for Landlock rule: {err}"
                ) from err
            try:
                st = os.fstat(fd)
                is_dir = stat.S_ISDIR(st.st_mode)
                rule = LandlockPathBeneathAttr()
                rule.allowed_access = (
                    ro_fs
                    if is_dir
                    else (get_file_fs_access(abi_version, write=False) & handled_fs)
                )
                rule.parent_fd = fd
                ret = libc.syscall(
                    add_nr,
                    rfd,
                    LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(rule),
                    0,
                )
                if ret != 0:
                    errno = ctypes.get_errno()
                    raise RuntimeError(
                        f"landlock_add_rule failed for read-only path '{path_str}' "
                        f"with errno {errno}"
                    )
            finally:
                os.close(fd)

        # Apply full read-write rules for workspace paths and devices
        for path_str in read_write_paths:
            try:
                fd = os.open(path_str, o_path | o_cloexec)
            except OSError as err:
                raise RuntimeError(
                    f"Failed to open read-write path '{path_str}' "
                    f"for Landlock rule: {err}"
                ) from err
            try:
                st = os.fstat(fd)
                is_dir = stat.S_ISDIR(st.st_mode)
                rule = LandlockPathBeneathAttr()
                rule.allowed_access = (
                    handled_fs
                    if is_dir
                    else (get_file_fs_access(abi_version, write=True) & handled_fs)
                )
                rule.parent_fd = fd
                ret = libc.syscall(
                    add_nr,
                    rfd,
                    LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(rule),
                    0,
                )
                if ret != 0:
                    errno = ctypes.get_errno()
                    raise RuntimeError(
                        f"landlock_add_rule failed for read-write path '{path_str}' "
                        f"with errno {errno}"
                    )
            finally:
                os.close(fd)

        # Seal and enforce the sandbox ruleset
        ret = libc.syscall(restrict_nr, rfd, 0)
        if ret != 0:
            errno = ctypes.get_errno()
            raise RuntimeError(f"landlock_restrict_self failed with errno {errno}")
    finally:
        try:
            os.close(rfd)
        except Exception:
            pass


class LandlockWorkspace(LocalWorkspace):
    """Containerless, daemon-less sandboxed workspace leveraging Linux Landlock LSM.

    Provides sub-4ms cold start execution with filesystem access control bounded
    to the working directory, unprivileged process isolation, and clean process
    tree extinction on timeout.

    When Linux Landlock LSM is unavailable (e.g., non-Linux systems or kernels
    without Landlock enabled), it gracefully falls back to unprivileged process
    grouping and directory-bound execution.
    """

    read_only_paths: list[str] = Field(
        default_factory=list,
        description="Additional paths to grant read-only access to under Landlock.",
    )
    read_write_paths: list[str] = Field(
        default_factory=list,
        description="Additional paths to grant read-write access to under Landlock.",
    )
    enable_landlock: bool = Field(
        default=True,
        description="Whether to enforce Landlock LSM if supported by the kernel.",
    )
    enable_process_group: bool = Field(
        default=True,
        description="Whether to isolate subprocesses in their own process group.",
    )
    unshare_user: bool = Field(
        default=False,
        description="Whether to run commands inside an unprivileged user namespace.",
    )

    _abi_version: int | None = PrivateAttr(default=None)

    def __init__(
        self,
        *,
        working_dir: str | Path,
        read_only_paths: list[str | Path] | None = None,
        read_write_paths: list[str | Path] | None = None,
        enable_landlock: bool = True,
        enable_process_group: bool = True,
        unshare_user: bool = False,
        **kwargs: Any,
    ):
        resolved_working_dir = str(Path(working_dir).resolve())
        ro_paths = [str(Path(p).resolve()) for p in (read_only_paths or [])]
        rw_paths = [str(Path(p).resolve()) for p in (read_write_paths or [])]
        init_data: dict[str, Any] = {
            "working_dir": resolved_working_dir,
            "read_only_paths": ro_paths,
            "read_write_paths": rw_paths,
            "enable_landlock": enable_landlock,
            "enable_process_group": enable_process_group,
            "unshare_user": unshare_user,
            **kwargs,
        }
        super().__init__(**init_data)

    @property
    def abi_version(self) -> int:
        """Return the detected Linux Landlock LSM ABI version (1-6+).

        Returns 0 if Landlock is unavailable, disabled, or not running on Linux.
        """
        if self._abi_version is None:
            self._abi_version = get_landlock_abi_version()
        return self._abi_version

    @property
    def is_landlock_supported(self) -> bool:
        """Return True if Landlock LSM is supported on this platform."""
        return self.abi_version >= 1

    @property
    def is_enforcing(self) -> bool:
        """Return True if Landlock LSM is active and enforcing access rules."""
        return self.enable_landlock and self.is_landlock_supported

    @property
    def fallback_mode(self) -> bool:
        """Return True if running in fallback mode without Landlock LSM enforcement."""
        return not self.is_enforcing

    def _collect_read_only_paths(self) -> list[str]:
        """Collect and validate all paths requiring read-only access."""
        paths: list[str] = []
        for p in DEFAULT_READ_ONLY_PATHS:
            try:
                resolved = str(Path(p).resolve())
            except Exception:
                resolved = p
            if os.path.exists(resolved) and resolved not in paths:
                paths.append(resolved)
        # Ensure active Python interpreter prefixes are readable
        for pfx in (sys.prefix, sys.base_prefix):
            if pfx and os.path.exists(pfx):
                try:
                    resolved_pfx = str(Path(pfx).resolve())
                except Exception:
                    resolved_pfx = pfx
                if (
                    not any(resolved_pfx.startswith(p) for p in paths)
                    and resolved_pfx not in paths
                ):
                    paths.append(resolved_pfx)
        for ep in self.read_only_paths:
            try:
                resolved_ep = str(Path(ep).resolve())
            except Exception:
                resolved_ep = ep
            if os.path.exists(resolved_ep) and resolved_ep not in paths:
                paths.append(resolved_ep)
        return paths

    def _collect_read_write_paths(self, cwd: Path) -> list[str]:
        """Collect and validate all paths requiring read-write access."""
        ws_root = Path(self.working_dir).resolve()
        paths: list[str] = [str(ws_root)]
        resolved_cwd = cwd.resolve()
        if resolved_cwd.is_relative_to(ws_root) and resolved_cwd.exists():
            resolved_cwd_str = str(resolved_cwd)
            if resolved_cwd_str not in paths:
                paths.append(resolved_cwd_str)
        # Standard pseudo-devices required for redirection and terminal/random access
        for dev in DEFAULT_READ_WRITE_DEVICES:
            try:
                resolved_dev = str(Path(dev).resolve())
            except Exception:
                resolved_dev = dev
            if os.path.exists(resolved_dev) and resolved_dev not in paths:
                paths.append(resolved_dev)
        # Dedicated workspace tmp location to keep transient files inside sandbox
        ws_tmp = ws_root / ".tmp"
        try:
            ws_tmp.mkdir(parents=True, exist_ok=True)
            if str(ws_tmp) not in paths:
                paths.append(str(ws_tmp))
        except Exception:
            pass
        for ep in self.read_write_paths:
            try:
                resolved_ep = str(Path(ep).resolve())
            except Exception:
                resolved_ep = ep
            if os.path.exists(resolved_ep) and resolved_ep not in paths:
                paths.append(resolved_ep)
        return paths

    def execute_command(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: float = 30.0,
    ) -> CommandResult:
        """Execute a bash command in the Landlock sandboxed workspace.

        Args:
            command: The command string to execute.
            cwd: Working directory (defaults to workspace working_dir).
            timeout: Maximum execution time in seconds.

        Returns:
            CommandResult: Result with exit_code, stdout, stderr, command, and
                timeout_occurred.
        """
        ws_root = Path(self.working_dir).resolve()
        if cwd is not None:
            effective_cwd = Path(cwd)
            if not effective_cwd.is_absolute():
                effective_cwd = (ws_root / effective_cwd).resolve()
            else:
                effective_cwd = effective_cwd.resolve()
            if not effective_cwd.is_relative_to(ws_root):
                raise ValueError(
                    f"Working directory '{cwd}' ({effective_cwd}) "
                    f"must be inside workspace root '{ws_root}'"
                )
        else:
            effective_cwd = ws_root

        # Ensure working directory exists before running command
        effective_cwd.mkdir(parents=True, exist_ok=True)

        ro_paths = self._collect_read_only_paths()
        rw_paths = self._collect_read_write_paths(effective_cwd)
        abi = self.abi_version if self.enable_landlock else 0

        # Check for unprivileged user namespace wrapping if requested
        use_unshare = self.unshare_user and is_unshare_user_supported()
        if use_unshare:
            cmd_to_run: list[str] | str = [
                "unshare",
                "-U",
                "-p",
                "-f",
                "bash",
                "-c",
                command,
            ]
            use_shell = False
        else:
            cmd_to_run = command
            use_shell = True

        logger.info("$ %s", redact_text_secrets(command))
        logger.debug(
            f"Landlock execution: ABI={abi}, enforcing={self.is_enforcing}, "
            f"cwd={effective_cwd}, timeout={timeout}"
        )

        def preexec() -> None:
            _apply_landlock_and_group(
                read_only_paths=ro_paths,
                read_write_paths=rw_paths,
                abi_version=abi,
                enable_process_group=self.enable_process_group,
            )

        proc_env = sanitized_env()
        ws_tmp = Path(self.working_dir) / ".tmp"
        if ws_tmp.exists():
            proc_env["TMPDIR"] = str(ws_tmp)
            proc_env["TEMP"] = str(ws_tmp)
            proc_env["TMP"] = str(ws_tmp)

        start_time = time.monotonic()
        proc = subprocess.Popen(
            cmd_to_run,
            cwd=str(effective_cwd),
            env=proc_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            shell=use_shell,
            preexec_fn=preexec,
        )

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        if proc.stdout is None or proc.stderr is None:
            raise RuntimeError("Failed to capture stdout/stderr streams")

        stop_event = threading.Event()

        def read_stream(stream: Any, lines: list[str], output_stream: Any) -> None:
            try:
                fd = stream.fileno()
                if hasattr(select, "poll"):
                    poll = select.poll()
                    poll.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
                    while not stop_event.is_set():
                        events = poll.poll(100)
                        if not events:
                            continue
                        line = stream.readline()
                        if not line:
                            break
                        output_stream.write(line)
                        output_stream.flush()
                        lines.append(line)
                else:
                    while not stop_event.is_set():
                        r, _, _ = select.select([fd], [], [], 0.1)
                        if not r:
                            continue
                        line = stream.readline()
                        if not line:
                            break
                        output_stream.write(line)
                        output_stream.flush()
                        lines.append(line)
            except Exception as exc:
                logger.error(f"Failed to read stream: {exc}")

        stdout_thread = threading.Thread(
            target=read_stream,
            args=(proc.stdout, stdout_lines, sys.stdout),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(proc.stderr, stderr_lines, sys.stderr),
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        timeout_occurred = False
        try:
            wait_timeout = (
                max(0.0, timeout - (time.monotonic() - start_time))
                if timeout is not None
                else None
            )
            proc.wait(timeout=wait_timeout)
        except subprocess.TimeoutExpired:
            timeout_occurred = True
            logger.warning(f"Command timed out after {timeout}s: {command}")

            # Escalated process group extinction: SIGTERM followed by SIGKILL
            if self.enable_process_group:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

                try:
                    proc.wait(timeout=0.2)
                except (subprocess.TimeoutExpired, Exception):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
            else:
                proc.kill()

            stop_event.set()
            stdout_thread.join(timeout=0.5)
            stderr_thread.join(timeout=0.5)

            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            if proc.stderr:
                try:
                    proc.stderr.close()
                except Exception:
                    pass

            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines),
                timeout_occurred=True,
            )

        # Process exited within wait timeout.
        # Join reader threads up to EOF within remaining overall timeout.
        if timeout is not None:
            remaining = max(0.0, timeout - (time.monotonic() - start_time))
        else:
            remaining = None

        stdout_thread.join(timeout=remaining)

        if timeout is not None:
            remaining = max(0.0, timeout - (time.monotonic() - start_time))
        else:
            remaining = None

        stderr_thread.join(timeout=remaining)

        if stdout_thread.is_alive() or stderr_thread.is_alive():
            logger.warning(
                f"Stream reader threads timed out after {timeout}s: {command}"
            )
            stop_event.set()
            timeout_occurred = True
            stdout_thread.join(timeout=0.1)
            stderr_thread.join(timeout=0.1)

        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass
        if proc.stderr:
            try:
                proc.stderr.close()
            except Exception:
                pass

        return CommandResult(
            command=command,
            exit_code=-1 if timeout_occurred else proc.returncode,
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            timeout_occurred=timeout_occurred,
        )

    def file_upload(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Upload (copy) a file into the workspace."""
        source = Path(source_path)
        destination = Path(destination_path)
        if not destination.is_absolute():
            destination = Path(self.working_dir) / destination

        logger.debug(f"Landlock file upload: {source} -> {destination}")

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return FileOperationResult(
                success=True,
                source_path=str(source),
                destination_path=str(destination),
                file_size=destination.stat().st_size,
            )
        except Exception as exc:
            logger.error(f"Landlock file upload failed: {exc}")
            return FileOperationResult(
                success=False,
                source_path=str(source),
                destination_path=str(destination),
                error=str(exc),
            )

    def file_download(
        self,
        source_path: str | Path,
        destination_path: str | Path,
    ) -> FileOperationResult:
        """Download (copy) a file from the workspace."""
        source = Path(source_path)
        if not source.is_absolute():
            source = Path(self.working_dir) / source
        destination = Path(destination_path)

        logger.debug(f"Landlock file download: {source} -> {destination}")

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return FileOperationResult(
                success=True,
                source_path=str(source),
                destination_path=str(destination),
                file_size=destination.stat().st_size,
            )
        except Exception as exc:
            logger.error(f"Landlock file download failed: {exc}")
            return FileOperationResult(
                success=False,
                source_path=str(source),
                destination_path=str(destination),
                error=str(exc),
            )

    def git_changes(self, path: str | Path) -> list[GitChange]:
        """Get git changes for a repository in the workspace."""
        target_path = Path(self.working_dir) / path
        return get_git_changes(target_path)

    def git_diff(self, path: str | Path) -> GitDiff:
        """Get git diff for a repository path in the workspace."""
        target_path = Path(self.working_dir) / path
        return get_git_diff(target_path)

    def pause(self) -> None:
        """Pause the workspace (no-op for process-isolated workspaces)."""
        logger.debug("pause() called on LandlockWorkspace - nothing to do")

    def resume(self) -> None:
        """Resume the workspace (no-op for process-isolated workspaces)."""
        logger.debug("resume() called on LandlockWorkspace - nothing to do")
