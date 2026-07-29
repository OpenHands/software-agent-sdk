"""Kernel-backed filesystem isolation for terminal child processes."""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Literal, cast

from openhands.sdk.logger import get_logger
from openhands.tools.utils import resolve_workspace_subpath


logger = get_logger(__name__)

TerminalSandboxMode = Literal["off", "auto", "required"]
TERMINAL_SANDBOX_ENV = "OH_TERMINAL_SANDBOX"
PUBLIC_READ_ROOTS = (
    "/agent-server/knowledge",
    "/agent-server/.agents/skills",
)

# Name of the AppArmor profile loaded into the kernel at image build time
# by `apparmor_parser -r -W /etc/apparmor.d/openhands-agent-terminal`.
# The profile file ships under openhands/tools/terminal/apparmor/ and is
# copied into /etc/apparmor.d/ by the Dockerfile.
APPARMOR_PROFILE_NAME = "openhands-agent-terminal"


def terminal_sandbox_mode() -> TerminalSandboxMode:
    """Return the configured terminal sandbox mode.

    Linux and macOS use a required kernel-backed sandbox by default. Windows
    remains disabled because this module has no Windows backend.
    """
    default: TerminalSandboxMode = (
        "required" if platform.system() in {"Linux", "Darwin"} else "off"
    )
    value = os.environ.get(TERMINAL_SANDBOX_ENV, default).lower()
    if value not in {"off", "auto", "required"}:
        raise ValueError(
            f"{TERMINAL_SANDBOX_ENV} must be one of: off, auto, required; got {value!r}"
        )
    return cast(TerminalSandboxMode, value)


def terminal_sandbox_enabled(mode: TerminalSandboxMode) -> bool:
    """Return whether this mode requires an isolated Unix terminal backend."""
    return mode != "off" and platform.system() in {"Linux", "Darwin"}


def _is_apparmor_available() -> bool:
    """Return whether the AppArmor terminal sandbox backend is usable.

    Requires three conditions, all checkable without privilege:
      1. ``aa-exec`` is on PATH (ships with ``apparmor-utils``).
      2. The kernel has the AppArmor LSM active (``/sys/kernel/security/lsm``
         contains ``apparmor``).
      3. The ``openhands-agent-terminal`` profile file is installed under
         ``/etc/apparmor.d/``. The Dockerfile loads it into the kernel at
         image build time via ``apparmor_parser``; we do not reload at
         runtime to avoid requiring CAP_MAC_ADMIN on the pod.
    """
    if platform.system() != "Linux":
        return False
    if shutil.which("aa-exec") is None:
        return False
    try:
        lsm = Path("/sys/kernel/security/lsm").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return False
    if "apparmor" not in lsm.split(","):
        return False
    profile_path = Path(f"/etc/apparmor.d/{APPARMOR_PROFILE_NAME}")
    return profile_path.is_file()


def _is_landlock_available() -> bool:
    """Return whether the Landlock terminal sandbox backend is usable.

    Landlock is applied via a small Python wrapper script that re-execs a
    Python interpreter (``sys.executable``) so ``py_landlock`` can apply the
    LSM policy before ``exec``-ing the shell. In PyInstaller mode
    (``sys.frozen=True``), ``sys.executable`` points at the frozen binary
    (e.g. ``/usr/local/bin/openhands-agent-server``) rather than a Python
    interpreter, so the wrapper's ``os.execv`` would feed CLI args the
    frozen binary cannot parse. Skip landlock in that case and let the
    caller fall back to AppArmor or bwrap, which are external binaries.
    """
    if getattr(sys, "frozen", False):
        return False
    try:
        import_module("py_landlock")
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _is_bwrap_usable() -> bool:
    """Return whether bwrap can actually run in this environment.

    Being on PATH is necessary but not sufficient: container seccomp
    profiles commonly block the ``mount`` syscall that bwrap requires to
    set up bind mounts and mount namespaces, even when ``--unshare-user-try``
    succeeds (seccomp does not understand user-namespace capability
    boundaries). This smoke test runs a minimal bwrap invocation to verify
    that mount operations actually work.
    """
    bwrap_path = shutil.which("bwrap")
    if bwrap_path is None:
        return False
    cmd = [bwrap_path, "--unshare-ipc", "--unshare-uts"]
    if os.geteuid() != 0:
        cmd.append("--unshare-user-try")
    for path in ("/usr", "/etc", "/lib", "/lib64", "/bin", "/sbin"):
        if Path(path).exists():
            cmd.extend(["--ro-bind", path, path])
    cmd.extend(["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "/usr/bin/env"])
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        logger.warning(
            "bwrap is on PATH but failed a smoke test (rc=%d, stderr=%s); "
            "the container's seccomp profile may be blocking mount syscalls",
            result.returncode,
            result.stderr.decode(errors="replace").strip()[:200],
        )
        return False
    return True


class TerminalSandbox:
    """Apply a platform-specific kernel policy before starting a shell.

    Backend selection (Linux):
      1. ``apparmor`` — LSM-based, no capability/namespace required. Requires
         ``aa-exec`` and a pre-loaded ``openhands-agent-terminal`` profile
         (the Dockerfile loads it at image build time via ``apparmor_parser``).
      2. ``bwrap`` (Bubblewrap) — user-namespace-based, no kernel feature required
      3. ``py_landlock`` — Landlock LSM (Linux 5.13+ kernel support needed)

    macOS always uses ``sandbox-exec`` (Seatbelt).
    """

    _backend: Literal["apparmor", "bwrap", "landlock", "seatbelt"] | None

    def __init__(
        self,
        work_dir: str,
        mode: TerminalSandboxMode,
        *,
        read_only_paths: tuple[str, ...] = (),
        read_write_paths: tuple[str, ...] | None = None,
    ):
        resolved_work_dir = Path(work_dir).resolve()
        self.work_dir = resolved_work_dir
        self.mode: TerminalSandboxMode = mode
        self._tmp_dir = resolved_work_dir / ".openhands-tmp"
        self.read_only_paths = tuple(
            resolve_workspace_subpath(p, resolved_work_dir) for p in read_only_paths
        )
        rw_paths = (
            read_write_paths if read_write_paths is not None else (resolved_work_dir,)
        )
        self.read_write_paths = tuple(
            resolve_workspace_subpath(p, resolved_work_dir) for p in rw_paths
        )
        self._backend = None
        self._landlock_wrapper: Path | None = None
        self._seatbelt_profile: Path | None = None
        self._apparmor_available: bool = False

    def prepare(self) -> None:
        """Probe available sandbox backends and prepare the chosen one."""
        if not terminal_sandbox_enabled(self.mode):
            return
        self._tmp_dir.mkdir(mode=0o700, exist_ok=True)
        if platform.system() == "Darwin":
            sandbox_exec = shutil.which("sandbox-exec")
            if sandbox_exec is None:
                if self.mode == "required":
                    raise RuntimeError(
                        "Terminal sandbox is required, but sandbox-exec is unavailable"
                    )
                logger.warning(
                    "sandbox-exec is unavailable; terminal sandbox is disabled"
                )
                return
            self._seatbelt_profile = self._tmp_dir / ".openhands-seatbelt.sb"
            self._seatbelt_profile.write_text(self._build_seatbelt_profile())
            self._backend = "seatbelt"
            return
        # Linux backend selection.
        #
        # When the caller passed conversation-scoped ``read_only_paths`` or
        # ``read_write_paths``, the sandbox is meant to enforce a per-conversation
        # PathAccessPolicy. bwrap and Landlock can express that by allowing
        # ``events/`` read-only and ``public_data/`` read-write. AppArmor uses a
        # single global denylist loaded at image build time, so use it only as a
        # fallback when neither per-conversation backend is available.
        #
        # Without a per-conversation policy, the default order stands:
        #   AppArmor (no capability / namespace) > bwrap > Landlock.
        has_conversation_policy = bool(
            self.read_only_paths or self.read_write_paths != (self.work_dir,)
        )
        backend_chosen = False

        # Conversation-scoped policy needs per-workspace mount semantics. Prefer
        # bwrap because it fails before exec with visible stderr; Landlock is
        # applied via a wrapper script that the sandbox writes in prepare().
        if has_conversation_policy:
            self._apparmor_available = _is_apparmor_available()
            if _is_bwrap_usable():
                self._backend = "bwrap"
                backend_chosen = True
                logger.info("Using bwrap terminal sandbox for per-conversation policy")

            if not backend_chosen:
                if _is_landlock_available():
                    self._backend = "landlock"
                    backend_chosen = True
                    logger.info(
                        "Using Landlock terminal sandbox for per-conversation policy%s",
                        " (+ AppArmor as defense-in-depth)"
                        if self._apparmor_available
                        else "",
                    )
                else:
                    logger.warning(
                        "Per-conversation policy requested but bwrap is unavailable "
                        "and Landlock is unavailable (py-landlock not importable or "
                        "PyInstaller frozen mode); falling back to AppArmor"
                    )
                if not backend_chosen and self._apparmor_available:
                    self._backend = "apparmor"
                    backend_chosen = True
                    logger.info(
                        "Using AppArmor terminal sandbox (profile=%s) as fallback",
                        APPARMOR_PROFILE_NAME,
                    )

        # No conversation policy → keep the legacy order: AppArmor first.
        if not backend_chosen and not has_conversation_policy:
            if _is_apparmor_available():
                self._backend = "apparmor"
                self._apparmor_available = True
                backend_chosen = True
                logger.info(
                    "Using AppArmor terminal sandbox (profile=%s)",
                    APPARMOR_PROFILE_NAME,
                )

        if not backend_chosen:
            if _is_bwrap_usable():
                self._backend = "bwrap"
                backend_chosen = True

        if not backend_chosen:
            if _is_landlock_available():
                self._backend = "landlock"
                backend_chosen = True
            elif self.mode == "required":
                raise RuntimeError(
                    "Terminal sandbox is required, but no backend is available "
                    "(AppArmor profile not loaded or LSM not exposed by container, "
                    "bwrap not installed or blocked by seccomp, "
                    "py-landlock not importable, or PyInstaller frozen mode "
                    "disables landlock). Consider setting OH_TERMINAL_SANDBOX=auto "
                    "or adjusting the container securityContext."
                )
            else:
                logger.warning("no sandbox backend available")

        if self._backend == "landlock":
            self._write_landlock_wrapper()

    def _write_landlock_wrapper(self) -> None:
        """Generate a wrapper script that applies landlock then execs the command.

        This avoids using preexec_fn, which is unsafe in multithreaded processes.
        """
        system_read_paths = [
            path
            for path in ("/usr", "/etc", "/lib", "/lib64", "/bin", "/sbin", "/dev")
            if Path(path).exists()
        ]
        public_read_paths = [path for path in PUBLIC_READ_ROOTS if Path(path).exists()]
        executable_paths = [
            path for path in ("/usr", "/bin", "/sbin") if Path(path).exists()
        ]
        policy = {
            "system_read_paths": system_read_paths,
            "public_read_paths": public_read_paths,
            "read_only_paths": [str(p) for p in self.read_only_paths],
            "executable_paths": executable_paths,
            "tmp_dir": str(self._tmp_dir),
            "read_write_paths": [str(p) for p in self.read_write_paths],
            "mode": self.mode,
        }
        policy_path = self._tmp_dir / ".openhands-landlock-policy.json"
        policy_path.write_text(json.dumps(policy))

        wrapper = self._tmp_dir / ".openhands-landlock-wrapper"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "\n"
            "parent_python = sys.argv[1]\n"
            "if os.path.realpath(sys.executable) != os.path.realpath(parent_python):\n"
            "    os.execv(parent_python, [parent_python, __file__, *sys.argv[1:]])\n"
            "\n"
            "import json\n"
            "from py_landlock import Landlock\n"
            f"policy = json.load(open({str(policy_path)!r}))\n"
            "try:\n"
            "    (\n"
            "        Landlock(strict=True)\n"
            "        .allow_read(*policy['system_read_paths'])\n"
            "        .allow_read(\n"
            "            *policy['public_read_paths'],\n"
            "            *policy['read_only_paths'],\n"
            "        )\n"
            "        .allow_write('/dev/null', '/dev/tty')\n"
            "        .allow_execute(*policy['executable_paths'])\n"
            "        .allow_read_write(\n"
            "            policy['tmp_dir'], *policy['read_write_paths']\n"
            "        )\n"
            "        .apply()\n"
            "    )\n"
            "except Exception as exc:\n"
            "    if policy['mode'] == 'required':\n"
            "        print(\n"
            "            f'Failed to apply Landlock policy: {exc}',\n"
            "            file=sys.stderr,\n"
            "        )\n"
            "        sys.exit(1)\n"
            "os.execvp(sys.argv[2], sys.argv[2:])\n"
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
        self._landlock_wrapper = wrapper

    def wrap_command(self, command: list[str]) -> list[str]:
        """Wrap a command with the platform-specific sandbox launcher."""
        # Landlock is applied by a small Python wrapper script that the
        # TerminalSandbox writes during prepare(); the wrapper execs the target
        # command so no extra process outlives the shell.  When AppArmor is
        # also loaded, aa-exec is the wrapper's first child so the process
        # runs under both LSMs.
        if self._backend == "landlock":
            assert self._landlock_wrapper is not None
            wrapper = str(self._landlock_wrapper)
            parent_python = sys.executable
            if self._apparmor_available:
                return [
                    wrapper,
                    parent_python,
                    "aa-exec",
                    "-p",
                    APPARMOR_PROFILE_NAME,
                    "--",
                    *command,
                ]
            return [wrapper, parent_python, *command]
        if self._backend == "apparmor":
            return [
                "aa-exec",
                "-p",
                APPARMOR_PROFILE_NAME,
                "--",
                *command,
            ]
        if self._backend == "bwrap":
            return self._build_bwrap_args() + command
        if self._backend == "seatbelt":
            sandbox_exec = shutil.which("sandbox-exec")
            if sandbox_exec is None:
                raise RuntimeError(
                    "sandbox-exec became unavailable after profile creation"
                )
            return [
                sandbox_exec,
                "-f",
                str(self._seatbelt_profile),
                "--",
                *command,
            ]
        return command

    def cleanup(self) -> None:
        """Remove the generated sandbox profile/wrapper after the shell exits."""
        if self._seatbelt_profile is not None:
            self._seatbelt_profile.unlink(missing_ok=True)
        if self._landlock_wrapper is not None:
            self._landlock_wrapper.unlink(missing_ok=True)
            policy = self._tmp_dir / ".openhands-landlock-policy.json"
            policy.unlink(missing_ok=True)

    def _build_bwrap_args(self) -> list[str]:
        args = ["bwrap", "--unshare-ipc", "--unshare-uts"]
        if os.geteuid() != 0:
            args.append("--unshare-user-try")
        for path in ("/usr", "/etc", "/lib", "/lib64", "/bin", "/sbin"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        for path in PUBLIC_READ_ROOTS:
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        args.extend(["--bind", str(self._tmp_dir), str(self._tmp_dir)])
        for path in self.read_write_paths:
            args.extend(["--bind", str(path), str(path)])
        for path in self.read_only_paths:
            if path.exists():
                args.extend(["--ro-bind", str(path), str(path)])
        args.extend(["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"])
        return args

    def _build_seatbelt_profile(self) -> str:
        parent = self._seatbelt_path(self.work_dir.parent)
        traversal_paths: set[Path] = set()
        for allowed_path in (
            self.work_dir,
            *self.read_write_paths,
            *self.read_only_paths,
            *(Path(path) for path in PUBLIC_READ_ROOTS),
        ):
            traversal_paths.add(allowed_path)
            traversal_paths.update(allowed_path.parents)
        return "\n".join(
            [
                "(version 1)",
                "(allow default)",
                f'(deny file-read* (subpath "{parent}"))',
                # Directory metadata is required to traverse an absolute path
                # into an allowed child. This does not expose sibling contents.
                *(
                    f'(allow file-read-metadata (literal "'
                    f'{self._seatbelt_path(path)}"))'
                    for path in sorted(traversal_paths, key=lambda item: str(item))
                ),
                *(
                    f'(allow file-read* (subpath "{self._seatbelt_path(path)}"))'
                    for path in (*self.read_write_paths, *self.read_only_paths)
                ),
                *(
                    f'(allow file-read* (subpath "{self._seatbelt_path(Path(path))}"))'
                    for path in PUBLIC_READ_ROOTS
                ),
                "(deny file-write*)",
                '(allow file-write* (literal "/dev/null"))',
                '(allow file-write* (literal "/dev/tty"))',
                *(
                    f'(allow file-write* (subpath "{self._seatbelt_path(path)}"))'
                    for path in self.read_write_paths
                ),
                "",
            ]
        )

    @staticmethod
    def _seatbelt_path(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace('"', '\\"')
