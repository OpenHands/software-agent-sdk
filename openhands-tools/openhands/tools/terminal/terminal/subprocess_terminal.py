"""PTY-based terminal backend implementation (replaces pipe-based subprocess)."""

import fcntl
import os
import pty
import re
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


def _normalize_eols(raw: bytes) -> bytes:
    # CRLF/LF/CR -> CR, so each logical line is terminated with \r for the TTY
    raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return ENTER.join(raw.split(b"\n"))


class SubprocessTerminal(TerminalInterface):
    """PTY-backed terminal backend.

    Creates an interactive bash in a pseudoterminal (PTY) so programs behave as if
    attached to a real terminal. Initialization uses a sentinel-based handshake
    and prompt detection instead of blind sleeps.
    """

    PS1: str
    process: subprocess.Popen | None
    _pty_master_fd: int | None
    output_buffer: deque[str]
    output_lock: threading.Lock
    reader_thread: threading.Thread | None
    _current_command_running: bool

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        shell_path: str | None = None,
    ):
        super().__init__(work_dir, username)
        self.PS1 = CmdOutputMetadata.to_ps1_prompt()
        self.process = None
        self._pty_master_fd = None
        # Use a slightly larger buffer to match tmux behavior which seems to keep
        # ~10,001 lines instead of exactly 10,000
        self.output_buffer = deque(maxlen=HISTORY_LIMIT + 50)  # Circular buffer
        self.output_lock = threading.Lock()
        self.reader_thread = None
        self._current_command_running = False
        self.shell_path = shell_path

    # ------------------------- Lifecycle -------------------------

    def initialize(self) -> None:
        """Initialize the PTY terminal session."""
        if self._initialized:
            return

        # Resolve shell path with precedence:
        # 1. Explicit shell_path argument
        # 2. Auto-detection via shutil.which("bash") (searches PATH like `env bash`)
        resolved_shell_path: str | None
        if self.shell_path:
            resolved_shell_path = self.shell_path
        else:
            resolved_shell_path = shutil.which("bash")
            if resolved_shell_path is None:
                raise RuntimeError(
                    "Could not find bash in PATH. "
                    "Please provide an explicit shell_path parameter "
                    "when creating the terminal."
                )

        # Validate the shell path exists and is executable
        if not os.path.isfile(resolved_shell_path):
            raise RuntimeError(
                f"Shell binary not found at: {resolved_shell_path}. "
                "Please provide a valid shell_path parameter."
            )
        if not os.access(resolved_shell_path, os.X_OK):
            raise RuntimeError(
                f"Shell binary is not executable: {resolved_shell_path}. "
                "Please check file permissions."
            )

        # Store the resolved shell path for later access
        self.shell_path = resolved_shell_path
        logger.info(f"Using shell: {resolved_shell_path}")

        # Inherit environment variables from the parent process
        env = os.environ.copy()
        env["PS1"] = self.PS1
        env["PS2"] = ""
        env["TERM"] = "xterm-256color"

        bash_cmd = [resolved_shell_path, "-i"]

        # Create a PTY; give the slave to the child, keep the master
        master_fd, slave_fd = pty.openpty()

        logger.debug("Initializing PTY terminal with: %s", " ".join(bash_cmd))
        try:
            self.process = subprocess.Popen(
                bash_cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.work_dir,
                env=env,
                text=False,  # bytes I/O
                bufsize=0,
                preexec_fn=os.setsid,  # new process group for signal handling
                close_fds=True,
            )
        finally:
            # Parent must close its copy of the slave FD
            try:
                os.close(slave_fd)
            except Exception:
                pass

        self._pty_master_fd = master_fd

        # Set master FD non-blocking
        flags = fcntl.fcntl(self._pty_master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._pty_master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Start output reader thread
        self.reader_thread = threading.Thread(
            target=self._read_output_continuously_pty, daemon=True
        )
        self.reader_thread.start()
        self._initialized: bool = True

        # Configure bash: disable history expansion, set up PS1/PS2 prompts
        init_cmd = (
            f'set +H; export PROMPT_COMMAND=\'export PS1="{self.PS1}"\'; export PS2=""'
        ).encode("utf-8", "ignore")

        self._write_pty(init_cmd + ENTER)
        time.sleep(1.0)  # Wait for command to take effect

        self.clear_screen()

        logger.debug("PTY terminal initialized with work dir: %s", self.work_dir)

    def close(self) -> None:
        """Clean up the PTY terminal."""
        if self._closed:
            return

        try:
            if self.process:
                # Try a graceful exit
                try:
                    self._write_pty(b"exit\n")
                except Exception:
                    pass
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # Escalate
                    try:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
        except Exception as e:
            logger.error(f"Error closing PTY terminal: {e}", exc_info=True)
        finally:
            # Reader thread stop: close master FD; thread exits on read error/EOF
            try:
                if self._pty_master_fd is not None:
                    os.close(self._pty_master_fd)
            except Exception:
                pass
            self._pty_master_fd = None

            if self.reader_thread and self.reader_thread.is_alive():
                self.reader_thread.join(timeout=1)

            self.process = None
            self._closed: bool = True

    # ------------------------- I/O Core -------------------------

    def _write_pty(self, data: bytes) -> None:
        if not self._initialized and self._pty_master_fd is None:
            # allow init path to call before _initialized flips
            raise RuntimeError("PTY master FD not ready")
        if self._pty_master_fd is None:
            raise RuntimeError("PTY terminal is not initialized")
        try:
            logger.debug(f"Wrote to subprocess PTY: {data!r}")
            os.write(self._pty_master_fd, data)
        except Exception as e:
            logger.error(f"Failed to write to PTY: {e}", exc_info=True)
            raise

    def _read_output_continuously_pty(self) -> None:
        """Continuously read output from the PTY master in a separate thread."""
        fd = self._pty_master_fd
        if fd is None:
            return

        try:
            while True:
                # Exit early if process died
                if self.process and self.process.poll() is not None:
                    break

                # Use select to avoid busy spin
                r, _, _ = select.select([fd], [], [], 0.1)
                if not r:
                    continue

                try:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break  # EOF
                    # Normalize newlines; PTY typically uses \n already
                    text = chunk.decode("utf-8", errors="replace")
                    with self.output_lock:
                        # Store one line per buffer item to make deque truncation work
                        self._add_text_to_buffer(text)
                except OSError:
                    # Would-block or FD closed
                    continue
                except Exception as e:
                    logger.debug(f"Error reading PTY output: {e}")
                    break
        except Exception as e:
            logger.error(f"PTY reader thread error: {e}", exc_info=True)

    def _add_text_to_buffer(self, text: str) -> None:
        """Add text to buffer, ensuring one line per buffer item."""
        # If there's a partial line in the last buffer item, combine with new text
        if self.output_buffer and not self.output_buffer[-1].endswith("\n"):
            combined_text = self.output_buffer[-1] + text
            self.output_buffer.pop()  # Remove the partial line
        else:
            combined_text = text

        # Split into lines and add each line as a separate buffer item
        lines = combined_text.split("\n")

        # Add all complete lines (all but the last, which might be partial)
        for line in lines[:-1]:
            self.output_buffer.append(line + "\n")

        # Add the last part (might be partial line)
        if lines[-1]:  # Only add if not empty
            self.output_buffer.append(lines[-1])

    # ------------------------- Readiness Helpers -------------------------

    def _wait_for_output(self, pattern: str | re.Pattern, timeout: float = 5.0) -> bool:
        """Wait until the output buffer contains pattern (regex or literal)."""
        deadline = time.time() + timeout
        is_regex = hasattr(pattern, "search")
        while time.time() < deadline:
            # quick yield to reader thread
            if self._pty_master_fd is not None:
                select.select([], [], [], 0.02)
            with self.output_lock:
                data = "".join(self.output_buffer)
            if is_regex:
                assert isinstance(pattern, re.Pattern)
                if pattern.search(data):
                    return True
            else:
                assert isinstance(pattern, str)
                if pattern in data:
                    return True
        return False

    def _wait_for_prompt(self, timeout: float = 5.0) -> bool:
        """Wait until the screen ends with our PS1 end marker (prompt visible)."""
        pat = re.compile(re.escape(CMD_OUTPUT_PS1_END.rstrip()) + r"\s*$")
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.output_lock:
                tail = "".join(self.output_buffer)[-4096:]
            if pat.search(tail):
                return True
            time.sleep(0.05)
        return False

    # ------------------------- Public API -------------------------

    def send_keys(self, text: str, enter: bool = True) -> None:
        """Send keystrokes to the PTY.

        Supports:
          - Plain text
          - Ctrl sequences: 'C-a'..'C-z' (Ctrl+C sends ^C byte)
          - Special names: 'ENTER','TAB','BS','ESC','UP','DOWN','LEFT','RIGHT',
                           'HOME','END','PGUP','PGDN','C-L','C-D'
        """
        if not self._initialized:
            raise RuntimeError("PTY terminal is not initialized")

        specials = {
            "ENTER": ENTER,
            "TAB": b"\t",
            "BS": b"\x7f",  # Backspace (DEL)
            "ESC": b"\x1b",
            "UP": b"\x1b[A",
            "DOWN": b"\x1b[B",
            "RIGHT": b"\x1b[C",
            "LEFT": b"\x1b[D",
            "HOME": b"\x1b[H",
            "END": b"\x1b[F",
            "PGUP": b"\x1b[5~",
            "PGDN": b"\x1b[6~",
            "C-L": b"\x0c",  # Ctrl+L
            "C-D": b"\x04",  # Ctrl+D (EOF)
        }

        upper = text.upper().strip()
        payload: bytes | None = None

        # Named specials
        if upper in specials:
            payload = specials[upper]
            # Do NOT auto-append another EOL; special already includes it when needed.
            append_eol = False
        # Generic Ctrl-<letter>, including C-C (preferred over sending SIGINT directly)
        elif upper.startswith(("C-", "CTRL-", "CTRL+")):
            # last char after dash/plus is the key
            key = upper.split("-", 1)[-1].split("+", 1)[-1]
            if len(key) == 1 and "A" <= key <= "Z":
                payload = bytes([ord(key) & 0x1F])
            else:
                # Unknown form; fall back to raw text
                payload = text.encode("utf-8", "ignore")
            append_eol = False  # ctrl combos are “instant”
        else:
            raw = text.encode("utf-8", "ignore")
            payload = _normalize_eols(raw) if enter else raw
            append_eol = enter and not payload.endswith(ENTER)

        if append_eol:
            payload += ENTER

        self._write_pty(payload)
        self._current_command_running = self._current_command_running or (
            append_eol or payload.endswith(ENTER)
        )

    def read_screen(self) -> str:
        """Read the current terminal screen content.

        The content we return should NOT contains carriage returns (CR, \r).
        """
        if not self._initialized:
            raise RuntimeError("PTY terminal is not initialized")

        # Give the reader thread a moment to capture any pending output
        # This is especially important after sending a command
        time.sleep(0.01)

        with self.output_lock:
            content = "".join(self.output_buffer)
            lines = content.split("\n")
            content = "\n".join(lines).replace("\r", "")
            logger.debug(f"Read from subprocess PTY: {content!r}")
            return content

    def clear_screen(self) -> None:
        """Drop buffered output up to the most recent PS1 block; do not emit ^L."""
        if not self._initialized:
            return

        need_prompt_nudge = False
        with self.output_lock:
            if not self.output_buffer:
                need_prompt_nudge = True
            else:
                data = "".join(self.output_buffer)
                start_idx = data.rfind(CMD_OUTPUT_PS1_BEGIN)
                end_idx = data.rfind(CMD_OUTPUT_PS1_END)
                if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                    tail = data[start_idx:]
                    self.output_buffer.clear()
                    self.output_buffer.append(tail)
                else:
                    self.output_buffer.clear()
                    need_prompt_nudge = True

        if need_prompt_nudge:
            try:
                self._write_pty(ENTER)  # ask bash to render a prompt, no screen clear
            except Exception:
                pass

    def interrupt(self) -> bool:
        """Send SIGINT to the PTY process group (fallback to signal-based interrupt)."""
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
        """Heuristic: command running if not at PS1 prompt and process alive."""
        if not self._initialized or not self.process:
            return False

        # Check if process is still alive
        if self.process.poll() is not None:
            return False

        try:
            content = self.read_screen()
            # If screen ends with prompt, no command is running
            return not content.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
        except Exception:
            return self._current_command_running
