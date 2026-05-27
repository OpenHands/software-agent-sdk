"""Hook executor - runs shell commands with JSON I/O."""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time

from pydantic import BaseModel

from openhands.sdk.hooks.config import HookDefinition
from openhands.sdk.hooks.types import HookDecision, HookEvent
from openhands.sdk.utils import sanitized_env


class HookResult(BaseModel):
    """Result from executing a hook.

    Exit-code semantics (matching Claude Code's hook contract):

    - **Exit 0**: success. ``stdout`` is parsed as JSON for structured output
      (``decision``, ``reason``, ``additionalContext``, ``continue``).
    - **Exit 2**: blocking error. The operation is denied / the agent is
      prevented from stopping. ``stderr`` should explain why.
    - **Any other non-zero exit code**: non-blocking error. ``success`` is set
      to ``False`` and the error is logged, but the operation still proceeds.
      In particular, exit code ``1`` does **not** block — only ``2`` does.
      Hooks intended to enforce a policy must exit with ``2``.
    """

    success: bool = True
    blocked: bool = False
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    decision: HookDecision | None = None
    reason: str | None = None
    additional_context: str | None = None
    error: str | None = None
    async_started: bool = False  # Indicates this was an async hook

    @property
    def should_continue(self) -> bool:
        """Whether the operation should continue after this hook."""
        if self.blocked:
            return False
        if self.decision == HookDecision.DENY:
            return False
        return True


logger = logging.getLogger(__name__)


class PersistentHookRunner:
    """A long-lived subprocess that proxies hook command execution.

    Instead of forking from the main (large-heap) Python process on every
    hook call, we fork once to create a lightweight runner, then send each
    hook invocation over JSON-line stdin/stdout IPC.  The runner's internal
    ``subprocess.run`` forks from a tiny heap, cutting per-call overhead.
    """

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------------

    def _start(self) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-m", "openhands.sdk.hooks._runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return proc

    def _ensure_alive(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            self._process = self._start()
        return self._process

    # -- public API -----------------------------------------------------------

    def run(
        self,
        command: str,
        cwd: str | None,
        env: dict[str, str] | None,
        input_data: str,
        timeout: int,
    ) -> dict:
        """Send a hook execution request and return the response dict.

        Raises ``RuntimeError`` if the runner is unreachable.
        """
        request = json.dumps(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "input": input_data,
                "timeout": timeout,
            }
        )

        with self._lock:
            proc = self._ensure_alive()
            assert proc.stdin is not None  # noqa: S101
            assert proc.stdout is not None  # noqa: S101
            try:
                proc.stdin.write(request + "\n")
                proc.stdin.flush()
                raw_line = proc.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                # Runner died between requests — restart for next caller.
                self._process = None
                raise RuntimeError(f"hook runner pipe broken: {exc}") from exc

        if not raw_line:
            self._process = None
            raise RuntimeError("hook runner exited unexpectedly")

        return json.loads(raw_line)

    def close(self) -> None:
        with self._lock:
            proc = self._process
            if proc is None:
                return
            self._process = None
        # Close stdin to signal EOF; the runner exits cleanly.
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


class AsyncProcessManager:
    """Manages background hook processes for cleanup.

    Tracks async hook processes and ensures they are terminated when they
    exceed their timeout or when the session ends. Prevents zombie processes
    by properly waiting for termination.
    """

    def __init__(self):
        self._processes: list[tuple[subprocess.Popen, float, int]] = []

    def add_process(self, process: subprocess.Popen, timeout: int) -> None:
        """Track a background process for cleanup.

        Args:
            process: The subprocess to track
            timeout: Maximum runtime in seconds before termination
        """
        self._processes.append((process, time.time(), timeout))

    def _terminate_process(self, process: subprocess.Popen) -> None:
        """Safely terminate a process group and prevent zombies.

        Uses process groups to kill the entire process tree, not just
        the parent shell when shell=True is used.
        """
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            return

        try:
            # Kill the entire process group (handles shell=True child processes)
            pgid = os.getpgid(process.pid)
        except (OSError, ProcessLookupError) as e:
            logger.debug(f"Process already terminated: {e}")
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
            process.wait(timeout=1)  # Wait for graceful termination
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)  # Force kill if it doesn't terminate
                process.wait()
            except OSError:
                pass
        except OSError as e:
            logger.debug(f"Failed to kill process group: {e}")

    def cleanup_expired(self) -> None:
        """Terminate processes that have exceeded their timeout."""
        current_time = time.time()
        active: list[tuple[subprocess.Popen, float, int]] = []
        for process, start_time, timeout in self._processes:
            if process.poll() is None:  # Still running
                if current_time - start_time > timeout:
                    logger.debug(f"Terminating expired async hook (PID {process.pid})")
                    self._terminate_process(process)
                else:
                    active.append((process, start_time, timeout))
            # If poll() returns non-None, process already exited - just drop it
        self._processes = active

    def cleanup_all(self) -> None:
        """Terminate all tracked background processes."""
        for process, _, _ in self._processes:
            if process.poll() is None:
                self._terminate_process(process)
        self._processes = []


class HookExecutor:
    """Executes hook commands with JSON I/O.

    Synchronous hooks are routed through a :class:`PersistentHookRunner` so
    that the main Python process forks only once (to spawn the runner).  All
    subsequent ``subprocess.run`` calls happen inside the runner's lightweight
    heap, eliminating hundreds of expensive fork+exec cycles per conversation.

    Async hooks still use ``subprocess.Popen`` directly because they are
    fire-and-forget and need independent process-group management.
    """

    def __init__(
        self,
        working_dir: str | None = None,
        async_process_manager: AsyncProcessManager | None = None,
        persistent_runner: PersistentHookRunner | None = None,
    ):
        self.working_dir = working_dir or os.getcwd()
        self.async_process_manager = async_process_manager or AsyncProcessManager()
        self._runner = persistent_runner or PersistentHookRunner()

    def close(self) -> None:
        """Shut down the persistent runner and clean up async processes."""
        self._runner.close()
        self.async_process_manager.cleanup_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_hook_output(hook_result: HookResult) -> HookResult:
        """Parse JSON from stdout into structured HookResult fields."""
        if not hook_result.stdout.strip():
            return hook_result
        try:
            output_data = json.loads(hook_result.stdout)
            if isinstance(output_data, dict):
                if "decision" in output_data:
                    decision_str = output_data["decision"].lower()
                    if decision_str == "allow":
                        hook_result.decision = HookDecision.ALLOW
                    elif decision_str == "deny":
                        hook_result.decision = HookDecision.DENY
                        hook_result.blocked = True
                if "reason" in output_data:
                    hook_result.reason = str(output_data["reason"])
                if "additionalContext" in output_data:
                    hook_result.additional_context = str(
                        output_data["additionalContext"]
                    )
                if "continue" in output_data:
                    if not output_data["continue"]:
                        hook_result.blocked = True
        except json.JSONDecodeError:
            pass
        return hook_result

    def _build_env(
        self,
        event: HookEvent,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        hook_env = sanitized_env()
        hook_env["OPENHANDS_PROJECT_DIR"] = self.working_dir
        hook_env["OPENHANDS_SESSION_ID"] = event.session_id or ""
        hook_env["OPENHANDS_EVENT_TYPE"] = event.event_type
        if event.tool_name:
            hook_env["OPENHANDS_TOOL_NAME"] = event.tool_name
        if extra:
            hook_env.update(extra)
        return hook_env

    def _execute_sync_via_runner(
        self,
        hook: HookDefinition,
        event_json: str,
        hook_env: dict[str, str],
    ) -> HookResult:
        """Execute a sync hook through the persistent runner process."""
        try:
            resp = self._runner.run(
                command=hook.command,
                cwd=self.working_dir,
                env=hook_env,
                input_data=event_json,
                timeout=hook.timeout,
            )
        except RuntimeError:
            logger.debug("Persistent runner unavailable, falling back to direct fork")
            return self._execute_sync_direct(hook, event_json, hook_env)

        timed_out: bool = resp.get("timed_out", False)
        error: str | None = resp.get("error")
        if timed_out:
            return HookResult(
                success=False,
                exit_code=-1,
                error=error or f"Hook timed out after {hook.timeout} seconds",
            )
        if error:
            return HookResult(success=False, exit_code=-1, error=error)

        exit_code: int = resp.get("exit_code", -1)
        hook_result = HookResult(
            success=exit_code == 0,
            blocked=exit_code == 2,
            exit_code=exit_code,
            stdout=resp.get("stdout", ""),
            stderr=resp.get("stderr", ""),
        )
        return self._parse_hook_output(hook_result)

    def _execute_sync_direct(
        self,
        hook: HookDefinition,
        event_json: str,
        hook_env: dict[str, str],
    ) -> HookResult:
        """Fallback: execute a sync hook via direct subprocess.run."""
        try:
            result = subprocess.run(
                hook.command,
                shell=True,
                cwd=self.working_dir,
                env=hook_env,
                input=event_json,
                capture_output=True,
                text=True,
                timeout=hook.timeout,
            )
            hook_result = HookResult(
                success=result.returncode == 0,
                blocked=result.returncode == 2,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
            return self._parse_hook_output(hook_result)
        except subprocess.TimeoutExpired:
            return HookResult(
                success=False,
                exit_code=-1,
                error=f"Hook timed out after {hook.timeout} seconds",
            )
        except FileNotFoundError as e:
            return HookResult(
                success=False,
                exit_code=-1,
                error=f"Hook command not found: {e}",
            )
        except Exception as e:
            return HookResult(
                success=False,
                exit_code=-1,
                error=f"Hook execution failed: {e}",
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        hook: HookDefinition,
        event: HookEvent,
        env: dict[str, str] | None = None,
    ) -> HookResult:
        """Execute a single hook."""
        hook_env = self._build_env(event, env)
        event_json = event.model_dump_json()

        # Cleanup expired async processes before starting new ones
        self.async_process_manager.cleanup_expired()

        # Handle async hooks: fire and forget (cannot use persistent runner)
        if hook.async_:
            try:
                creationflags = 0
                start_new_session = True
                if os.name == "nt":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    start_new_session = False

                process = subprocess.Popen(
                    hook.command,
                    shell=True,
                    cwd=self.working_dir,
                    env=hook_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=start_new_session,
                    creationflags=creationflags,
                )
                try:
                    if process.stdin and process.poll() is None:
                        process.stdin.write(event_json.encode())
                        process.stdin.flush()
                        process.stdin.close()
                except (BrokenPipeError, OSError) as e:
                    logger.warning(f"Failed to write to async hook stdin: {e}")

                self.async_process_manager.add_process(process, hook.timeout)
                logger.debug(f"Started async hook (PID {process.pid}): {hook.command}")

                return HookResult(
                    success=True,
                    exit_code=0,
                    async_started=True,
                )
            except Exception as e:
                return HookResult(
                    success=False,
                    exit_code=-1,
                    error=f"Failed to start async hook: {e}",
                )

        # Sync hooks — route through the persistent runner
        return self._execute_sync_via_runner(hook, event_json, hook_env)

    def execute_all(
        self,
        hooks: list[HookDefinition],
        event: HookEvent,
        env: dict[str, str] | None = None,
        stop_on_block: bool = True,
    ) -> list[HookResult]:
        """Execute multiple hooks in order, optionally stopping on block."""
        results: list[HookResult] = []

        # Cleanup expired async processes periodically
        self.async_process_manager.cleanup_expired()

        for hook in hooks:
            result = self.execute(hook, event, env)
            results.append(result)

            if stop_on_block and result.blocked:
                break

        return results
