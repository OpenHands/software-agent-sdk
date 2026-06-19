"""Process manager for exec_command / write_stdin interactive terminal tools."""

import threading
import time
from weakref import WeakValueDictionary

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import TerminalSession, create_terminal_session


# Per-conversation manager cache.
# When ExecCommandTool and WriteStdinTool are each created standalone for the
# same ConversationState, this ensures they share the same InteractiveTerminalManager
# so exec_command session IDs remain valid for write_stdin calls.
# WeakValueDictionary lets the manager be garbage-collected once all executors
# that hold a strong reference to it are torn down.
_per_conversation_managers: WeakValueDictionary[str, "InteractiveTerminalManager"] = (
    WeakValueDictionary()
)
_managers_lock = threading.Lock()


def get_or_create_manager(conv_id: str, work_dir: str) -> "InteractiveTerminalManager":
    """Return the shared manager for *conv_id*, creating one if necessary.

    The lock prevents a TOCTOU race where two concurrent create() calls for the
    same conv_id could each see None and allocate separate managers, leaving one
    orphaned with permanently unreachable sessions.
    """
    with _managers_lock:
        manager = _per_conversation_managers.get(conv_id)
        if manager is None:
            manager = InteractiveTerminalManager(work_dir)
            _per_conversation_managers[conv_id] = manager
    return manager


# Yield-time bounds that match Codex's unified-exec constants.
_MIN_YIELD_SECONDS: float = 0.25  # 250 ms
_MAX_YIELD_SECONDS: float = 30.0  # 30 s
# Minimum yield for an empty-stdin poll (no characters written).
_MIN_EMPTY_POLL_YIELD_SECONDS: float = 5.0  # 5 s
# Approximate chars per LLM token — used for max_output_tokens truncation.
_CHARS_PER_TOKEN: int = 4

# Maps both raw control bytes and OpenHands-style key names to the canonical
# OpenHands special-key strings recognised by TerminalSession.send_keys().
_CONTROL_CHAR_MAP: dict[str, str] = {
    "\x03": "C-c",  # ETX / Ctrl+C (raw byte)
    "\x04": "C-d",  # EOT / Ctrl+D (raw byte)
    "\x1a": "C-z",  # SUB / Ctrl+Z (raw byte)
    "C-c": "C-c",  # OpenHands-style passthrough
    "C-d": "C-d",
    "C-z": "C-z",
}


def _clamp_yield(yield_time_ms: int, *, is_empty_poll: bool = False) -> float:
    seconds = yield_time_ms / 1000.0
    if is_empty_poll:
        seconds = max(seconds, _MIN_EMPTY_POLL_YIELD_SECONDS)
    return max(_MIN_YIELD_SECONDS, min(seconds, _MAX_YIELD_SECONDS))


class InteractiveTerminalManager:
    """Manages multiple concurrent terminal sessions for background processes.

    Sessions are created on ``exec_command`` and referenced by integer
    ``session_id`` in subsequent ``write_stdin`` calls.  A session is removed
    automatically once the underlying process exits.
    """

    def __init__(self, work_dir: str) -> None:
        self._work_dir = work_dir
        self._sessions: dict[int, TerminalSession] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def exec_command(
        self,
        cmd: str,
        *,
        workdir: str | None = None,
        yield_time_ms: int = 10_000,
        max_output_tokens: int | None = None,
    ) -> tuple[str, float, int | None, int | None, int | None]:
        """Start *cmd* in a new session and return after *yield_time_ms*.

        Returns:
            ``(output, wall_time_seconds, session_id, exit_code, original_token_count)``

            Exactly one of ``session_id`` / ``exit_code`` is not ``None``:

            * ``session_id`` is set when the process is **still running** —
              pass it to ``write_stdin`` to poll or send input.
            * ``exit_code`` is set when the process has **completed**.
        """
        session = create_terminal_session(work_dir=workdir or self._work_dir)
        session.initialize()
        session_id = self._allocate_id()
        with self._lock:
            self._sessions[session_id] = session

        yield_s = _clamp_yield(yield_time_ms)
        action = TerminalAction(command=cmd, timeout=yield_s)

        t0 = time.monotonic()
        obs = session.execute(action)
        wall = time.monotonic() - t0

        return self._build_result(obs, wall, session, session_id, max_output_tokens)

    def write_stdin(
        self,
        session_id: int,
        *,
        chars: str = "",
        yield_time_ms: int = 5_000,
        max_output_tokens: int | None = None,
    ) -> tuple[str, float, int | None, int | None, int | None]:
        """Send *chars* to session *session_id* and return after *yield_time_ms*.

        Pass ``chars=""`` to poll for new output without writing anything.

        Returns the same 5-tuple as :meth:`exec_command`.
        """
        with self._lock:
            session = self._sessions.get(session_id)

        if session is None:
            msg = (
                f"No running session with session_id={session_id}. "
                "The process may have already completed or was never started."
            )
            return msg, 0.0, None, None, None

        is_empty_poll = chars == ""
        yield_s = _clamp_yield(yield_time_ms, is_empty_poll=is_empty_poll)

        mapped: str | None = None
        if is_empty_poll:
            action = TerminalAction(command="", is_input=False, timeout=yield_s)
        else:
            # Map common control characters to OpenHands special-key names.
            mapped = _CONTROL_CHAR_MAP.get(chars)
            if mapped is not None:
                action = TerminalAction(command=mapped, is_input=True, timeout=yield_s)
            else:
                # Deliver chars to the process stdin verbatim — no Enter appended.
                # This preserves the byte contract: chars="abc" delivers "abc",
                # chars="abc\n" delivers "abc\n". Callers append "\n" explicitly
                # when they want Enter, matching Codex's write_stdin semantics.
                action = TerminalAction(command=chars, is_input=True, timeout=yield_s)

        # Raw (non-special) stdin must reach the process byte-for-byte, so disable
        # the Enter-append behaviour that TerminalTool uses for interactive prompts.
        # Special keys ignore append_enter anyway (is_special_key forces enter=False),
        # and empty polls send nothing, so this only affects the raw-chars path.
        raw_stdin = not is_empty_poll and mapped is None
        t0 = time.monotonic()
        obs = session.execute(action, append_enter=not raw_stdin)
        wall = time.monotonic() - t0

        return self._build_result(obs, wall, session, session_id, max_output_tokens)

    def interrupt(self) -> None:
        """Send interrupt (Ctrl+C) to all active sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                session.interrupt()
            except Exception:  # noqa: BLE001
                pass

    def close(self) -> None:
        """Close all managed sessions. Safe to call more than once."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _allocate_id(self) -> int:
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            return sid

    def _build_result(
        self,
        obs: object,  # TerminalObservation — avoid circular import at module level
        wall: float,
        session: TerminalSession,
        session_id: int,
        max_output_tokens: int | None,
    ) -> tuple[str, float, int | None, int | None, int | None]:
        from openhands.tools.terminal.definition import TerminalObservation

        assert isinstance(obs, TerminalObservation)
        full_output = obs.text or ""
        # Compute token count BEFORE truncation so original_token_count is accurate.
        original_token_count = len(full_output) // _CHARS_PER_TOKEN or None
        output = (
            full_output[: max_output_tokens * _CHARS_PER_TOKEN]
            if max_output_tokens is not None
            else full_output
        )

        if session.is_running():
            return output, wall, session_id, None, original_token_count

        # Process finished — remove from the sessions map and close the session
        # so the underlying tmux pane / subprocess shell is torn down promptly.
        with self._lock:
            self._sessions.pop(session_id, None)
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

        # metadata.exit_code is the real exit code when PS1 appeared, or -1
        # if the process was interrupted before the prompt could be captured.
        ec = obs.metadata.exit_code
        return output, wall, None, ec, original_token_count
