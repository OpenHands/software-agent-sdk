import re
import threading
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Literal

from libtmux.exc import LibTmuxException, TmuxObjectDoesNotExist

from openhands.sdk.llm import TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool import ToolExecutor


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation
from openhands.tools.terminal.constants import CMD_OUTPUT_PS1_END
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)
from openhands.tools.terminal.terminal.factory import (
    _is_tmux_available,
    create_terminal_session,
)
from openhands.tools.terminal.terminal.terminal_session import (
    TerminalCommandStatus,
    TerminalSession,
)
from openhands.tools.terminal.terminal.tmux_pane_pool import (
    DEFAULT_MAX_PANES,
    PooledTmuxTerminal,
    TmuxPanePool,
)


_TMUX_POOL_RECOVERY_MESSAGE = (
    "The terminal session was reset because the underlying tmux server/session "
    "disappeared while running the previous command. This often happens when a "
    "command terminates the persistent shell, for example by ending with a "
    "top-level `exit` such as `exit $code`, or otherwise kills tmux. OpenHands "
    "rebuilt the terminal pool, but the interrupted command's result is not "
    "reliable and was not retried. Avoid top-level `exit` in future terminal "
    'commands; use a non-shell-exiting status check like `test "$code" -eq 0` '
    "or conditional shell logic instead. Please rerun any needed command."
)

_TMUX_RECOVERABLE_ERROR_MARKERS = (
    "no server running",
    "can't find session",
    "could not find window_id",
    "could not find pane_id",
)

logger = get_logger(__name__)

# Environment variable names must be alphanumeric + underscores, starting with
# a letter or underscore. This guards against shell injection via key names.
_ENV_VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class TerminalExecutor(ToolExecutor[TerminalAction, TerminalObservation]):
    shell_path: str | None

    def __init__(
        self,
        working_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int | None = None,
        terminal_type: Literal["tmux", "subprocess", "powershell"] | None = None,
        shell_path: str | None = None,
        full_output_save_dir: str | None = None,
        max_panes: int = DEFAULT_MAX_PANES,
    ):
        """Initialize TerminalExecutor with auto-detected or specified session type.

        Args:
            working_dir: Working directory for shell commands
            username: Optional username for the shell session
            no_change_timeout_seconds: Timeout for no output change
            terminal_type: Force a specific session type:
                         ('tmux', 'subprocess', or 'powershell').
                         If None, auto-detect based on system capabilities.
            shell_path: Path to the shell binary. On Unix this applies to the
                       subprocess backend; on Windows it can point to a
                       PowerShell executable.
            full_output_save_dir: Path to directory to save full output
                                  logs and files, used when truncation is needed.
            max_panes: Maximum number of concurrent panes in pool mode.
        """
        self.shell_path = shell_path
        self._working_dir = working_dir
        self._username = username
        self._no_change_timeout_seconds = no_change_timeout_seconds
        self._terminal_type = terminal_type
        self._max_panes = max_panes
        self.full_output_save_dir: str | None = full_output_save_dir

        # Pool mode: use TmuxPanePool for parallel execution
        self._pool: TmuxPanePool | None = None
        self._session: TerminalSession | None = None
        self._sessions: dict[int, TerminalSession] = {}
        self._sessions_lock = threading.Lock()
        self._pool_recovery_lock = threading.Lock()

        use_pool = terminal_type in (None, "tmux") and _is_tmux_available()

        if use_pool:
            self._initialize_pool()
        else:
            self._session = create_terminal_session(
                work_dir=working_dir,
                username=username,
                no_change_timeout_seconds=no_change_timeout_seconds,
                terminal_type=terminal_type,
                shell_path=shell_path,
            )
            self._session.initialize()
            logger.info(
                f"TerminalExecutor initialized with "
                f"working_dir: {working_dir}, "
                f"username: {username}, "
                f"terminal_type: "
                f"{terminal_type or self._session.__class__.__name__}"
            )

    @property
    def is_pooled(self) -> bool:
        """Whether this executor is using the tmux pane pool for concurrency."""
        return self._pool is not None

    def _initialize_pool(self) -> None:
        self._pool = TmuxPanePool(
            self._working_dir,
            self._username,
            max_panes=self._max_panes,
        )
        self._pool.initialize()
        logger.info(
            f"TerminalExecutor initialized (pool mode) "
            f"working_dir: {self._working_dir}, username: {self._username}, "
            f"max_panes: {self._max_panes}"
        )

    @staticmethod
    def _is_recoverable_tmux_pool_error(error: Exception) -> bool:
        recoverable_types = (LibTmuxException, TmuxObjectDoesNotExist)
        if not isinstance(error, recoverable_types):
            return False
        message = " ".join(str(arg) for arg in error.args).lower()
        return any(marker in message for marker in _TMUX_RECOVERABLE_ERROR_MARKERS)

    def _recover_tmux_pool(self, failed_pool: TmuxPanePool) -> None:
        with self._pool_recovery_lock:
            if self._pool is not failed_pool:
                return

            with suppress(Exception):
                failed_pool.close()
            with self._sessions_lock:
                self._sessions.clear()
            self._initialize_pool()

    @staticmethod
    def _tmux_pool_recovery_observation(
        action: TerminalAction,
        error: Exception,
    ) -> TerminalObservation:
        return TerminalObservation.from_text(
            text=(f"{_TMUX_POOL_RECOVERY_MESSAGE}\n\nOriginal tmux error: {error}"),
            is_error=True,
            command=action.command or "[RESET]",
            exit_code=-1,
        )

    @property
    def working_dir(self) -> str:
        """Return the working directory for this executor."""
        return self._working_dir

    @property
    def session(self) -> TerminalSession:
        """Access the single-session terminal.

        Raises:
            AttributeError: If the executor is in pool mode.
        """
        if self._pool is not None:
            raise AttributeError(
                "TerminalExecutor.session is not available in pool mode. "
                "Use the is_pooled property to check mode, or set "
                "terminal_type='subprocess' to disable pool mode."
            )
        assert self._session is not None
        return self._session

    # ------------------------------------------------------------------
    # Pool helpers
    # ------------------------------------------------------------------

    def _wrap_session(self, terminal: PooledTmuxTerminal) -> TerminalSession:
        """Get or create a TerminalSession for a pooled PooledTmuxTerminal."""
        pane_id = id(terminal)
        with self._sessions_lock:
            if pane_id not in self._sessions:
                # The pool already initialized the terminal — use
                # attach_to_existing to skip session.initialize() which
                # would create a duplicate tmux session.
                session = TerminalSession.attach_to_existing(
                    terminal, self._no_change_timeout_seconds
                )
                self._sessions[pane_id] = session
            return self._sessions[pane_id]

    def _discard_session(self, terminal: PooledTmuxTerminal) -> None:
        """Remove cached TerminalSession for a terminal being replaced.

        We mark the session (and its underlying terminal) as closed
        *before* dropping the reference.  This prevents
        ``TerminalSessionBase.__del__`` from calling ``close()`` which
        would kill the pooled terminal's window — and potentially the
        entire shared tmux session if that window is the last one.
        """
        with self._sessions_lock:
            session = self._sessions.pop(id(terminal), None)
            if session is not None:
                session._closed = True
                # Also mark the terminal so the pooled close() is a no-op
                terminal._closed = True

    @staticmethod
    def _prepare_pooled_session(session: TerminalSession) -> None:
        """Reset mutable session state so this checkout is independent.

        Without this, leftover ``prev_status`` from a timed-out command
        would cause the next independent call to be treated as a
        follow-up interaction, and stale screen content could corrupt
        PS1 counting.
        """
        if session.prev_status in (
            TerminalCommandStatus.NO_CHANGE_TIMEOUT,
            TerminalCommandStatus.HARD_TIMEOUT,
            TerminalCommandStatus.CONTINUE,
        ):
            # Previous command didn't finish — interrupt and poll until
            # the prompt reappears instead of sleeping a fixed duration.
            session.terminal.interrupt()
            _max_wait = 2.0
            _poll = 0.05
            _waited = 0.0
            while _waited < _max_wait:
                time.sleep(_poll)
                _waited += _poll
                screen = session.terminal.read_screen()
                if screen.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip()):
                    break
            else:
                logger.debug(
                    "Prompt did not reappear within %.1fs after interrupt; "
                    "proceeding anyway",
                    _max_wait,
                )
            session.terminal.clear_screen()
        session.prev_status = None
        session.prev_output = ""

    @staticmethod
    def _powershell_quote(value: str) -> str:
        escaped = value.replace("'", "''")
        return f"'{escaped}'"

    @staticmethod
    def _bash_quote(value: str) -> str:
        """Quote a value for bash using $'...' ANSI-C quoting."""
        escaped = value.replace("\\", "\\\\")
        escaped = escaped.replace("'", "\\'")
        escaped = escaped.replace("\n", "\\n")
        escaped = escaped.replace("\r", "\\r")
        escaped = escaped.replace("\t", "\\t")
        return f"$'{escaped}'"

    @classmethod
    def _build_env_exports(
        cls,
        env_vars: dict[str, str],
        session: TerminalSession,
    ) -> str:
        valid: dict[str, str] = {}
        for key, value in env_vars.items():
            if _ENV_VAR_NAME_RE.match(key):
                valid[key] = value
            else:
                logger.warning("Skipping secret with invalid env var name: %r", key)

        if not valid:
            return ""

        if session.terminal.is_powershell():
            assignments = [
                f"$env:{key} = {cls._powershell_quote(value)}"
                for key, value in valid.items()
            ]
            return "; ".join(assignments)

        assignments = [
            f"export {key}={cls._bash_quote(value)}" for key, value in valid.items()
        ]
        return " && ".join(assignments)

    # ------------------------------------------------------------------
    # Env export / secret masking
    # ------------------------------------------------------------------

    def _export_envs(
        self,
        action: TerminalAction,
        conversation: "LocalConversation | None" = None,
        session: TerminalSession | None = None,
    ) -> None:
        if not action.command.strip():
            return

        if action.is_input:
            return

        # Get secrets from conversation
        env_vars = {}
        if conversation is not None:
            try:
                secret_registry = conversation.state.secret_registry
                env_vars = secret_registry.get_secrets_as_env_vars(action.command)
            except Exception:
                env_vars = {}

        if not env_vars:
            return

        target = session or self.session
        exports_cmd = self._build_env_exports(env_vars, target)

        if not exports_cmd:
            return

        logger.debug(f"Exporting {len(env_vars)} environment variables before command")
        # Execute the export command separately to persist env in the session
        _ = target.execute(
            TerminalAction(
                command=exports_cmd,
                is_input=False,
                timeout=action.timeout,
            )
        )

    def _mask_observation(
        self,
        observation: TerminalObservation,
        conversation: "LocalConversation | None" = None,
    ) -> TerminalObservation:
        """Apply automatic secrets masking to *observation*."""
        content_text = observation.text

        if content_text and conversation is not None:
            try:
                secret_registry = conversation.state.secret_registry
                masked_content = secret_registry.mask_secrets_in_output(content_text)
                if masked_content:
                    data = observation.model_dump(
                        exclude={"content", "full_output_save_dir"}
                    )
                    return TerminalObservation.from_text(
                        text=masked_content,
                        full_output_save_dir=self.full_output_save_dir,
                        **data,
                    )
            except Exception:
                pass

        return observation

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset(self) -> TerminalObservation:
        """Public reset – delegates to the appropriate backend."""
        return self._reset_single_session()

    def _reset_single_session(self) -> TerminalObservation:
        """Reset the single-session terminal."""
        assert self._session is not None
        original_work_dir = self._session.work_dir
        original_username = self._session.username
        original_no_change_timeout = self._session.no_change_timeout_seconds

        self._session.close()
        self._session = create_terminal_session(
            work_dir=original_work_dir,
            username=original_username,
            no_change_timeout_seconds=original_no_change_timeout,
            terminal_type=None,
            shell_path=self.shell_path,
        )
        self._session.initialize()

        logger.info(
            f"Terminal session reset successfully with working_dir: {self._working_dir}"
        )

        return TerminalObservation.from_text(
            text=(
                "Terminal session has been reset. All previous environment "
                "variables and session state have been cleared."
            ),
            command="[RESET]",
            exit_code=0,
        )

    _RESET_TEXT = (
        "Terminal session has been reset. All previous environment "
        "variables and session state have been cleared."
    )

    # ------------------------------------------------------------------
    # Execution paths
    # ------------------------------------------------------------------

    def _execute_single_session(
        self,
        action: TerminalAction,
        conversation: "LocalConversation | None" = None,
    ) -> TerminalObservation:
        """Execute *action* in single-session (non-pool) mode."""
        if action.reset or self.session._closed:
            reset_result = self._reset_single_session()

            if action.command.strip():
                session = self.session  # reset created a fresh one
                command_action = TerminalAction(
                    command=action.command,
                    timeout=action.timeout,
                    is_input=False,
                )
                self._export_envs(command_action, conversation, session=session)
                command_result = session.execute(command_action)

                reset_text = reset_result.text
                command_text = command_result.text

                observation = command_result.model_copy(
                    update={
                        "content": [
                            TextContent(text=f"{reset_text}\n\n{command_text}")
                        ],
                        "command": f"[RESET] {action.command}",
                    }
                )
            else:
                observation = reset_result
        else:
            self._export_envs(action, conversation, session=self.session)
            observation = self.session.execute(action)

        return self._mask_observation(observation, conversation)

    def _execute_pooled(
        self,
        action: TerminalAction,
        conversation: "LocalConversation | None" = None,
    ) -> TerminalObservation:
        """Execute *action* in pool mode with proper checkout/checkin.

        All pane lifecycle (checkout, optional replace, checkin) is
        managed by the pool's context manager so there is exactly one
        checkout and one checkin per call.
        """
        pool = self._pool
        assert pool is not None
        try:
            with pool.pane() as handle:
                reset_text: str | None = None

                if action.reset or handle.terminal._closed:
                    self._discard_session(handle.terminal)
                    handle.terminal = pool.replace(handle.terminal)
                    reset_text = self._RESET_TEXT
                    logger.info(
                        "Terminal pane replaced (reset) "
                        f"working_dir: {self._working_dir}"
                    )

                    if not action.command.strip():
                        return TerminalObservation.from_text(
                            text=reset_text,
                            command="[RESET]",
                            exit_code=0,
                        )

                session = self._wrap_session(handle.terminal)
                self._prepare_pooled_session(session)

                cmd_action = (
                    action
                    if reset_text is None
                    else TerminalAction(
                        command=action.command,
                        timeout=action.timeout,
                        is_input=False,
                    )
                )
                self._export_envs(cmd_action, conversation, session=session)
                observation = session.execute(cmd_action)

                if reset_text is not None:
                    observation = observation.model_copy(
                        update={
                            "content": [
                                TextContent(text=f"{reset_text}\n\n{observation.text}")
                            ],
                            "command": f"[RESET] {action.command}",
                        }
                    )

                return self._mask_observation(observation, conversation)
        except Exception as error:
            if not self._is_recoverable_tmux_pool_error(error):
                raise
            logger.warning(
                "Recovering terminal pane pool after tmux server/session disappeared",
                exc_info=True,
            )
            self._recover_tmux_pool(pool)
            return self._tmux_pool_recovery_observation(action, error)

    def __call__(
        self,
        action: TerminalAction,
        conversation: "LocalConversation | None" = None,
    ) -> TerminalObservation:
        if action.reset and action.is_input:
            raise ValueError("Cannot use reset=True with is_input=True")

        if self._pool is not None:
            return self._execute_pooled(action, conversation)
        else:
            return self._execute_single_session(action, conversation)

    def close(self) -> None:
        """Close the terminal session and clean up resources."""
        if self._pool is not None:
            self._pool.close()
            with self._sessions_lock:
                self._sessions.clear()
        elif self._session is not None:
            self._session.close()
