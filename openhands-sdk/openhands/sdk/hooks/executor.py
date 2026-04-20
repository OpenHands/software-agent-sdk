"""Hook executor - runs shell commands with JSON I/O."""

import json
import logging
import os
import signal
import subprocess
import time

from pydantic import BaseModel

from openhands.sdk.hooks.config import HookDefinition
from openhands.sdk.hooks.types import HookDecision, HookEvent
from openhands.sdk.utils import sanitized_env


class HookResult(BaseModel):
    """Result from executing a hook.

    Exit code 0 = success, exit code 2 = block operation.
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
    """Executes hook commands with JSON I/O."""

    def __init__(
        self,
        working_dir: str | None = None,
        async_process_manager: AsyncProcessManager | None = None,
    ):
        self.working_dir = working_dir or os.getcwd()
        self.async_process_manager = async_process_manager or AsyncProcessManager()

    def execute(
        self,
        hook: HookDefinition,
        event: HookEvent,
        env: dict[str, str] | None = None,
    ) -> HookResult:
        """Execute a single hook."""
        # Prepare environment
        hook_env = sanitized_env()
        hook_env["OPENHANDS_PROJECT_DIR"] = self.working_dir
        hook_env["OPENHANDS_SESSION_ID"] = event.session_id or ""
        hook_env["OPENHANDS_EVENT_TYPE"] = event.event_type
        if event.tool_name:
            hook_env["OPENHANDS_TOOL_NAME"] = event.tool_name

        if env:
            hook_env.update(env)

        # Serialize event to JSON for stdin
        event_json = event.model_dump_json()

        # Cleanup expired async processes before starting new ones
        self.async_process_manager.cleanup_expired()

        # Handle async hooks: fire and forget
        if hook.async_:
            try:
                process = subprocess.Popen(
                    hook.command,
                    shell=True,
                    cwd=self.working_dir,
                    env=hook_env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,  # Create new process group for cleanup
                )
                # Write event JSON to stdin safely
                try:
                    if process.stdin and process.poll() is None:
                        process.stdin.write(event_json.encode())
                        process.stdin.flush()
                        process.stdin.close()
                except (BrokenPipeError, OSError) as e:
                    logger.warning(f"Failed to write to async hook stdin: {e}")

                # Track for cleanup
                self.async_process_manager.add_process(process, hook.timeout)
                logger.debug(f"Started async hook (PID {process.pid}): {hook.command}")

                # Return placeholder success result
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

        try:
            # Execute the hook command synchronously
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

            # Parse the result
            hook_result = HookResult(
                success=result.returncode == 0,
                blocked=result.returncode == 2,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )

            # Try to parse JSON from stdout
            if result.stdout.strip():
                try:
                    output_data = json.loads(result.stdout)
                    if isinstance(output_data, dict):
                        # Parse decision
                        if "decision" in output_data:
                            decision_str = output_data["decision"].lower()
                            if decision_str == "allow":
                                hook_result.decision = HookDecision.ALLOW
                            elif decision_str == "deny":
                                hook_result.decision = HookDecision.DENY
                                hook_result.blocked = True

                        # Parse other fields
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
                    # Not JSON, that's okay - just use stdout as-is
                    pass

            return hook_result

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
