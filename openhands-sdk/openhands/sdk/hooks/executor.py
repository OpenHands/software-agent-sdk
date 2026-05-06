"""Hook executor - runs shell commands and agent evaluations with JSON I/O."""

import concurrent.futures
import json
import logging
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING

import opentelemetry.context as otel_context
from pydantic import BaseModel

from openhands.sdk.hooks.config import HookDefinition, HookType
from openhands.sdk.hooks.types import HookDecision, HookEvent
from openhands.sdk.observability.laminar import observe
from openhands.sdk.utils import sanitized_env


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM


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
    """Executes hook commands and agent evaluations with JSON I/O."""

    def __init__(
        self,
        working_dir: str | None = None,
        async_process_manager: AsyncProcessManager | None = None,
        llm: "LLM | None" = None,
    ):
        self.working_dir = working_dir or os.getcwd()
        self.async_process_manager = async_process_manager or AsyncProcessManager()
        self.llm = llm

    @observe(
        name="hook.execute.agent",
        ignore_inputs=["self", "hook", "event"],
        ignore_output=True,
    )
    def _execute_agent_hook(
        self,
        hook: HookDefinition,
        event: HookEvent,
    ) -> HookResult:
        """Execute an agent-based hook by spawning a short-lived sub-conversation.

        The sub-conversation inherits the parent LLM, runs with read-only tools
        and no hooks (preventing recursion), and must return a JSON decision.
        """
        # Lazy imports to avoid circular dependency:
        # executor <- manager <- conversation_hooks <- local_conversation -> executor
        from openhands.sdk.agent import Agent  # type: ignore[attr-defined]
        from openhands.sdk.conversation.impl.local_conversation import LocalConversation
        from openhands.sdk.conversation.response_utils import get_agent_final_response
        from openhands.sdk.tool.spec import Tool

        event_type = (
            event.event_type
            if isinstance(event.event_type, str)
            else event.event_type.value
        )

        if self.llm is None:
            logger.warning(
                f"Agent hook has no LLM configured for event '{event_type}'"
                " — defaulting to allow"
            )
            return HookResult(
                success=True,
                decision=HookDecision.ALLOW,
                reason="No LLM configured for agent hook",
            )

        no_policy = "No additional policy specified. Use your best judgment."
        system_prompt = (
            "You are a security policy evaluator for an AI coding agent. "
            "Your only job is to decide whether an agent operation"
            " should be allowed or denied.\n\n"
            f"## Hook Event\n```json\n{event.model_dump_json(indent=2)}\n```\n\n"
            "## Instructions\n"
            "Use the available tools to investigate if needed (e.g. read files"
            " in the working directory). Then call `finish` with a JSON string"
            " as the message:\n"
            '  {"decision": "allow" | "deny", "reason": "<brief explanation>"}\n\n'
            "When in doubt, prefer `allow`. Only deny when the operation clearly"
            " violates the policy below.\n\n"
            f"## Policy\n{hook.prompt or no_policy}"
        )

        # Isolated LLM copy — hook metrics not attributed to the parent conversation.
        hook_llm = self.llm.model_copy(update={"usage_id": "hook-agent-evaluator"})
        hook_llm.reset_metrics()

        # Capture the current OTel span context so the sub-conversation thread
        # becomes a child of this span rather than an orphaned trace.
        parent_ctx = otel_context.get_current()

        conversation = None
        try:
            agent = Agent(
                llm=hook_llm,
                tools=[
                    Tool(name="GrepTool"),
                    Tool(name="GlobTool"),
                ],
                include_default_tools=["FinishTool"],
                system_prompt=system_prompt,
            )
            # hook_config=None disables all hooks in the sub-conversation (no recursion)
            conversation = LocalConversation(
                agent=agent,
                workspace=self.working_dir,
                plugins=None,
                hook_config=None,
                persistence_dir=None,
                visualizer=None,
                max_iteration_per_run=10,
            )
            conversation.send_message(
                f"Evaluate this {event_type} hook event and make your decision."
            )

            def _run_with_context() -> None:
                token = otel_context.attach(parent_ctx)
                try:
                    conversation.run()
                finally:
                    otel_context.detach(token)

            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(_run_with_context)
                try:
                    future.result(timeout=hook.timeout)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        f"Agent hook timed out after {hook.timeout}s"
                        f" for event '{event_type}' — defaulting to allow"
                    )
                    return HookResult(
                        success=False,
                        decision=HookDecision.ALLOW,
                        reason=(
                            f"Agent hook timed out after {hook.timeout} seconds"
                            " — defaulting to allow"
                        ),
                    )
            finally:
                # Don't block waiting for the thread — the conversation may still be
                # running but we've already returned or moved on.
                pool.shutdown(wait=False)
            raw = get_agent_final_response(conversation.state.events)
        except Exception as e:
            logger.warning(
                f"Agent hook sub-conversation failed for event '{event_type}'"
                f" — defaulting to allow: {e}"
            )
            return HookResult(
                success=False,
                decision=HookDecision.ALLOW,
                reason="Agent hook execution failed — defaulting to allow",
                error=str(e),
            )
        finally:
            if conversation is not None:
                conversation.close()

        if not raw:
            logger.warning(
                f"Agent hook produced no final response for event '{event_type}'"
                " — defaulting to allow"
            )
            return HookResult(
                success=False,
                decision=HookDecision.ALLOW,
                reason="Agent hook produced no final response — defaulting to allow",
            )

        try:
            data = json.loads(raw)
            decision_str = str(data.get("decision", "allow")).lower()
            reason = str(data.get("reason", ""))
            if decision_str == "deny":
                return HookResult(
                    success=True,
                    blocked=True,
                    decision=HookDecision.DENY,
                    reason=reason,
                )
            return HookResult(
                success=True,
                decision=HookDecision.ALLOW,
                reason=reason,
            )
        except (json.JSONDecodeError, AttributeError):
            logger.warning(
                f"Agent hook returned non-JSON response for event '{event_type}'"
                f" — defaulting to allow: {repr(raw)[:200]}"
            )
            return HookResult(
                success=True,
                decision=HookDecision.ALLOW,
                reason="Agent hook returned non-JSON response — defaulting to allow",
            )

    def execute(
        self,
        hook: HookDefinition,
        event: HookEvent,
        env: dict[str, str] | None = None,
    ) -> HookResult:
        """Execute a single hook."""
        if hook.type == HookType.AGENT:
            return self._execute_agent_hook(hook, event)
        if hook.type == HookType.PROMPT:
            event_type = (
                event.event_type
                if isinstance(event.event_type, str)
                else event.event_type.value
            )
            logger.warning(
                f"PROMPT hooks are not yet implemented — defaulting to allow"
                f" (event_type={event_type})"
            )
            return HookResult(
                success=False,
                decision=HookDecision.ALLOW,
                reason="PROMPT hooks are not yet implemented — defaulting to allow",
            )

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
