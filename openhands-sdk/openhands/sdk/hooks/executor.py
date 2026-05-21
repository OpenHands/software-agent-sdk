"""Hook executor - runs shell commands and agent evaluations with JSON I/O."""

import json
import logging
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel

from openhands.sdk.conversation.visualizer import ConversationVisualizerBase
from openhands.sdk.hooks.config import HookDefinition, HookType
from openhands.sdk.hooks.types import HookDecision, HookEvent
from openhands.sdk.observability.laminar import observe
from openhands.sdk.utils import sanitized_env


if TYPE_CHECKING:
    from openhands.sdk.llm import LLM


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
        persistence_dir: str | None = None,
        visualizer: type[ConversationVisualizerBase] | ConversationVisualizerBase | None = None,
    ):
        self.working_dir = working_dir or os.getcwd()
        self.async_process_manager = async_process_manager or AsyncProcessManager()
        self.llm = llm
        self.persistence_dir = persistence_dir
        self.visualizer = visualizer

    def _allow(
        self,
        reason: str,
        *,
        success: bool = False,
        error: str | None = None,
    ) -> HookResult:
        return HookResult(
            success=success,
            decision=HookDecision.ALLOW,
            reason=reason,
            error=error,
        )

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
            return self._allow("No LLM configured for agent hook")

        hook_llm = self.llm.model_copy(
            update={
                "usage_id": f"hook-agent:{hook.name or 'default'}",
                "timeout": hook.timeout,
            }
        )

        conversation = None
        try:
            agent = Agent(
                llm=hook_llm,
                tools=[Tool(name=t) for t in hook.tools],
                include_default_tools=["FinishTool"],
                system_prompt=hook.system_prompt,
            )
            # hook_config=None disables hooks in the sub-conversation (no recursion)
            conversation = LocalConversation(
                agent=agent,
                workspace=self.working_dir,
                plugins=None,
                hook_config=None,
                persistence_dir=self.persistence_dir,
                visualizer=self.visualizer,
                max_iteration_per_run=hook.max_iterations,
            )
            conversation.send_message(
                f"Evaluate this {event_type} hook event and make your decision.\n\n"
                f"## Hook Event\n```json\n{event.model_dump_json(indent=2)}\n```"
            )
            conversation.run()
            raw = get_agent_final_response(conversation.state.events)
        except Exception as e:
            logger.warning(
                f"Agent hook sub-conversation failed for event '{event_type}'"
                f" — defaulting to allow: {e}"
            )
            return self._allow(
                "Agent hook execution failed — defaulting to allow",
                error=str(e),
            )
        finally:
            if conversation is not None:
                conversation.close()

        return self._parse_decision(raw, event_type)

    _JSON_DECODER = json.JSONDecoder()

    def _extract_first_json_object(self, text: str) -> dict | None:
        # Walks the string and asks the JSON decoder to parse from each '{'.
        # Handles bare JSON, ```json ... ``` fences, prose prefixes/suffixes,
        # and mixed combinations — anything LLMs add around the actual object.
        for i, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                obj, _ = self._JSON_DECODER.raw_decode(text[i:])
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        return None

    def _parse_decision(self, raw: str, event_type: str) -> HookResult:
        if not raw:
            logger.warning(
                f"Agent hook produced no final response for event '{event_type}'"
                " — defaulting to allow"
            )
            return self._allow(
                "Agent hook produced no final response — defaulting to allow"
            )

        data = self._extract_first_json_object(raw)
        if data is None:
            logger.warning(
                f"Agent hook returned no parseable JSON object for event"
                f" '{event_type}' — defaulting to allow: {repr(raw)[:200]}"
            )
            return self._allow(
                "Agent hook returned no parseable JSON — defaulting to allow",
                success=True,
            )

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
