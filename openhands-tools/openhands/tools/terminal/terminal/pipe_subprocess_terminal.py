"""Pipe-based terminal backend for CI environments where PTY doesn't work."""

import fcntl
import os
import select
import shutil
import signal
import subprocess
import threading
import time
from collections import deque

from openhands.sdk.logger import get_logger
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_PS1_BEGIN,
    CMD_OUTPUT_PS1_END,
    HISTORY_LIMIT,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal import TerminalInterface


logger = get_logger(__name__)

ENTER = b"\n"


class PipeSubprocessTerminal(TerminalInterface):
    """Pipe-based terminal backend for CI environments.

    Uses subprocess with PIPE for stdin/stdout/stderr instead of PTY.
    This is more reliable in CI environments (Bitbucket, GitHub Actions, etc.)
    where PTY reads don't work properly.

    Trade-offs vs PTY:
    - No ANSI color support (TERM is set to 'dumb')
    - No interactive TTY features (readline, etc.)
    - But reliably captures output in any environment
    """

    PS1: str
    process: subprocess.Popen | None
    output_buffer: deque[str]
    output_lock: threading.Lock
    reader_thread: threading.Thread | None
    _current_command_running: bool
    _stdout_fd: int | None

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        shell_path: str | None = None,
    ):
        super().__init__(work_dir, username)
        self.PS1 = CmdOutputMetadata.to_ps1_prompt()
        self.process = None
        self.output_buffer = deque(maxlen=HISTORY_LIMIT + 50)
        self.output_lock = threading.Lock()
        self.reader_thread = None
        self._current_command_running = False
        self.shell_path = shell_path
        self._stdout_fd = None
        self._stop_reader = threading.Event()

    def initialize(self) -> None:
        """Initialize the pipe-based terminal session."""
        if self._initialized:
            return

        # Resolve shell path
        resolved_shell_path: str | None
        if self.shell_path:
            resolved_shell_path = self.shell_path
        else:
            resolved_shell_path = shutil.which("bash")
            if resolved_shell_path is None:
                raise RuntimeError(
                    "Could not find bash in PATH. "
                    "Please provide an explicit shell_path parameter."
                )

        if not os.path.isfile(resolved_shell_path):
            raise RuntimeError(f"Shell binary not found at: {resolved_shell_path}")
        if not os.access(resolved_shell_path, os.X_OK):
            raise RuntimeError(f"Shell binary not executable: {resolved_shell_path}")

        self.shell_path = resolved_shell_path
        logger.info(f"Using shell (pipe mode): {resolved_shell_path}")

        # Set up environment - use 'dumb' terminal to avoid ANSI escapes
        env = os.environ.copy()
        env["PS1"] = self.PS1
        env["PS2"] = ""
        env["TERM"] = "dumb"  # No ANSI escapes
        env["BASH_SILENCE_DEPRECATION_WARNING"] = "1"

        # Use non-interactive mode with explicit PS1 to avoid startup issues
        bash_cmd = [resolved_shell_path, "--noediting", "-i"]

        logger.debug("Initializing pipe terminal with: %s", " ".join(bash_cmd))

        self.process = subprocess.Popen(
            bash_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            cwd=self.work_dir,
            env=env,
            text=False,
            bufsize=0,
            preexec_fn=os.setsid,
        )

        # Set stdout to non-blocking for select-based reading
        if self.process.stdout:
            fd = self.process.stdout.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self._stdout_fd = fd
        else:
            self._stdout_fd = None

        # Start output reader thread
        self._stop_reader.clear()
        self.reader_thread = threading.Thread(
            target=self._read_output_continuously, daemon=True
        )
        self.reader_thread.start()
        self._initialized = True

        # Configure bash
        init_cmd = (
            "set +H; "
            f"export PROMPT_COMMAND='export PS1=\"{self.PS1}\"'; "
            'export PS2=""\n'
        ).encode("utf-8", "ignore")

        self._write_stdin(init_cmd)
        time.sleep(1.0)  # Wait for initialization

        self.clear_screen()
        logger.debug("Pipe terminal initialized with work dir: %s", self.work_dir)

    def close(self) -> None:
        """Clean up the pipe terminal."""
        if self._closed:
            return

        self._stop_reader.set()

        try:
            if self.process:
                try:
                    self._write_stdin(b"exit\n")
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except Exception as e:
            logger.error(f"Error closing pipe terminal: {e}", exc_info=True)
        finally:
            if self.reader_thread and self.reader_thread.is_alive():
                self.reader_thread.join(timeout=1)
            self.process = None
            self._closed = True

    def _write_stdin(self, data: bytes) -> None:
        """Write data to subprocess stdin."""
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("Pipe terminal is not initialized")
        try:
            logger.debug(f"Wrote to subprocess stdin: {data!r}")
            self.process.stdin.write(data)
            self.process.stdin.flush()
        except Exception as e:
            logger.error(f"Failed to write to stdin: {e}", exc_info=True)
            raise

    def _read_output_continuously(self) -> None:
        """Continuously read output from subprocess stdout using select."""
        if self._stdout_fd is None:
            logger.error("Pipe reader: stdout fd is None")
            return

        logger.debug(f"Pipe reader thread started, fd={self._stdout_fd}")

        try:
            while not self._stop_reader.is_set():
                # Check if process is still alive
                if self.process and self.process.poll() is not None:
                    logger.debug("Pipe reader: process ended")
                    break

                # Use select to wait for data (same pattern as PTY terminal)
                try:
                    r, _, _ = select.select([self._stdout_fd], [], [], 0.1)
                except (ValueError, OSError):
                    # FD closed or invalid
                    break

                if not r:
                    continue

                try:
                    chunk = os.read(self._stdout_fd, 4096)
                    if not chunk:
                        logger.debug("Pipe reader: EOF")
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    logger.debug(f"Pipe reader got {len(chunk)} bytes: {text[:100]!r}")
                    with self.output_lock:
                        self._add_text_to_buffer(text)
                except BlockingIOError:
                    # No data available (non-blocking)
                    continue
                except OSError as e:
                    logger.debug(f"Pipe reader OSError: {e}")
                    break
        except Exception as e:
            logger.error(f"Pipe reader thread error: {e}", exc_info=True)

    def _add_text_to_buffer(self, text: str) -> None:
        """Add text to buffer, ensuring one line per buffer item."""
        if self.output_buffer and not self.output_buffer[-1].endswith("\n"):
            combined_text = self.output_buffer[-1] + text
            self.output_buffer.pop()
        else:
            combined_text = text

        lines = combined_text.split("\n")

        for line in lines[:-1]:
            self.output_buffer.append(line + "\n")

        if lines[-1]:
            self.output_buffer.append(lines[-1])

    def send_keys(self, text: str, enter: bool = True) -> None:
        """Send keystrokes to the terminal."""
        if not self._initialized:
            raise RuntimeError("Pipe terminal is not initialized")

        # Handle special keys - in pipe mode, most don't work but we handle basic ones
        specials = {
            "ENTER": ENTER,
            "C-C": b"\x03",
            "C-D": b"\x04",
            "C-Z": b"\x1a",
        }

        upper = text.upper().strip()

        if upper in specials:
            payload = specials[upper]
            append_eol = False
        elif upper.startswith(("C-", "CTRL-", "CTRL+")):
            key = upper.split("-", 1)[-1].split("+", 1)[-1]
            if len(key) == 1 and "A" <= key <= "Z":
                payload = bytes([ord(key) & 0x1F])
            else:
                payload = text.encode("utf-8", "ignore")
            append_eol = False
        else:
            payload = text.encode("utf-8", "ignore")
            append_eol = enter and not payload.endswith(ENTER)

        if append_eol:
            payload += ENTER

        self._write_stdin(payload)
        self._current_command_running = self._current_command_running or (
            append_eol or payload.endswith(ENTER)
        )

    def read_screen(self) -> str:
        """Read the current terminal screen content."""
        if not self._initialized:
            raise RuntimeError("Pipe terminal is not initialized")

        # Give reader thread time to capture output
        time.sleep(0.05)

        with self.output_lock:
            content = "".join(self.output_buffer)
            content = content.replace("\r", "")
            logger.debug(f"Read from pipe subprocess: {content!r}")
            return content

    def clear_screen(self) -> None:
        """Clear the output buffer up to the most recent PS1 block."""
        if not self._initialized:
            return

        with self.output_lock:
            if not self.output_buffer:
                return

            data = "".join(self.output_buffer)
            start_idx = data.rfind(CMD_OUTPUT_PS1_BEGIN)
            end_idx = data.rfind(CMD_OUTPUT_PS1_END)

            if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                tail = data[start_idx:]
                self.output_buffer.clear()
                self.output_buffer.append(tail)
            else:
                self.output_buffer.clear()

    def interrupt(self) -> bool:
        """Send SIGINT to the process group."""
        if not self._initialized or not self.process:
            return False

        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
            self._current_command_running = False
            return True
        except Exception as e:
            logger.error(f"Failed to interrupt subprocess: {e}", exc_info=True)
            return False

    def is_running(self) -> bool:
        """Check if a command is currently running."""
        if not self._initialized or not self.process:
            return False

        if self.process.poll() is not None:
            return False

        try:
            content = self.read_screen()
            return not content.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
        except Exception:
            return self._current_command_running
