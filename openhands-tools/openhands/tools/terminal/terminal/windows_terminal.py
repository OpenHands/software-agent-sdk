"""Windows-compatible terminal backend implementation."""

import codecs
import json
import re
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

# Constants
CTRL_C = "\x03"
SCREEN_CLEAR_DELAY = 0.2
SETUP_DELAY = 0.5
SETUP_POLL_INTERVAL = 0.05
MAX_SETUP_WAIT = 2.0
READ_CHUNK_SIZE = 1024
POWERSHELL_CMD = ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", "-"]
READER_THREAD_TIMEOUT = 1.0
SPECIAL_KEYS = {CTRL_C, "C-c", "C-C"}


class WindowsTerminal(TerminalInterface):
    """Windows-compatible terminal backend.

    Uses subprocess with PIPE communication for Windows systems.
    """

    process: subprocess.Popen[bytes] | None
    output_buffer: deque[str]
    output_lock: threading.Lock
    reader_thread: threading.Thread | None
    _command_running_event: threading.Event
    _stop_reader: bool
    _decoder: codecs.IncrementalDecoder

    def __init__(self, work_dir: str, username: str | None = None):
        """Initialize Windows terminal.

        Args:
            work_dir: Working directory for commands
            username: Optional username (unused on Windows)
        """
        super().__init__(work_dir, username)
        self.process = None
        self.output_buffer = deque(maxlen=HISTORY_LIMIT)
        self.output_lock = threading.Lock()
        self.reader_thread = None
        self._command_running_event = threading.Event()
        self._stop_reader = False
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def initialize(self) -> None:
        """Initialize the Windows terminal session."""
        if self._initialized:
            return

        self._start_session()
        self._initialized = True

    def _start_session(self) -> None:
        """Start PowerShell session."""
        # Use PowerShell for better Windows compatibility
        startupinfo = subprocess.STARTUPINFO()  # type: ignore[attr-defined]
        # Hide the console window (prevents popup on Windows)
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW  # type: ignore[attr-defined]

        self.process = subprocess.Popen(
            POWERSHELL_CMD,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.work_dir,
            text=False,
            bufsize=0,
            startupinfo=startupinfo,
        )

        # Start reader thread
        self._stop_reader = False
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()

        # Set up PowerShell prompt
        self._setup_prompt()

    def _setup_prompt(self) -> None:
        """Configure PowerShell prompt."""
        # For PowerShell, we'll append the PS1 marker to each command instead of
        # using a custom prompt function, since prompt output isn't reliably captured
        # Wait for PowerShell initialization (copyright, welcome messages) to complete
        start_time = time.time()
        while time.time() - start_time < MAX_SETUP_WAIT:
            time.sleep(SETUP_POLL_INTERVAL)
            # Check if we have any output yet (indicates PowerShell is ready)
            with self.output_lock:
                if len(self.output_buffer) > 0:
                    break

        # Additional small delay for stability
        time.sleep(SETUP_DELAY)

        with self.output_lock:
            self.output_buffer.clear()

    def _write_to_stdin(self, data: str) -> None:
        """Write data to stdin."""
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(data.encode("utf-8"))
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                logger.error(f"Failed to write to stdin: {e}")

    def _read_output(self) -> None:
        """Read output from process in background thread."""
        if not self.process or not self.process.stdout:
            return

        # Cache stdout reference to prevent race condition during close()
        stdout = self.process.stdout

        while not self._stop_reader:
            try:
                # Read in chunks
                chunk = stdout.read(READ_CHUNK_SIZE)
                if not chunk:
                    break

                # Use incremental decoder to handle UTF-8 boundary splits correctly
                decoded = self._decoder.decode(chunk, False)
                if decoded:  # Only append non-empty strings
                    with self.output_lock:
                        self.output_buffer.append(decoded)

            except (ValueError, OSError) as e:
                # Expected when stdout is closed
                logger.debug(f"Output reading stopped: {e}")
                break
            except Exception as e:
                logger.error(f"Error reading output: {e}")
                break

        # Flush any remaining bytes when stopping
        try:
            final = self._decoder.decode(b"", True)
            if final:
                with self.output_lock:
                    self.output_buffer.append(final)
        except Exception as e:
            logger.error(f"Error flushing decoder: {e}")

    def _get_buffered_output(self, clear: bool = True) -> str:
        """Get all buffered output.

        Args:
            clear: Whether to clear the buffer after reading
        """
        with self.output_lock:
            # Create list copy to avoid race conditions during join
            buffer_copy = list(self.output_buffer)
            if clear:
                self.output_buffer.clear()
            return "".join(buffer_copy)

    def _is_special_key(self, text: str) -> bool:
        """Check if text is a special key sequence.

        Args:
            text: Text to check

        Returns:
            True if special key
        """
        return text in SPECIAL_KEYS

    def _escape_powershell_string(self, s: str) -> str:
        """Escape a string for safe use in PowerShell single quotes.

        In PowerShell single-quoted strings, only the single quote character
        needs escaping (by doubling it).

        Args:
            s: String to escape

        Returns:
            Escaped string with single quotes doubled
        """
        # In PowerShell single quotes, only single quote needs escaping
        return s.replace("'", "''")

    def _parse_metadata(self, output: str) -> CmdOutputMetadata | None:
        """Extract metadata from command output.

        Args:
            output: Command output containing metadata markers

        Returns:
            Parsed metadata or None if not found/invalid
        """
        pattern = (
            f"{re.escape(CMD_OUTPUT_PS1_BEGIN)}(.+?){re.escape(CMD_OUTPUT_PS1_END)}"
        )
        match = re.search(pattern, output, re.DOTALL)
        if match:
            try:
                meta_json = json.loads(match.group(1).strip())
                return CmdOutputMetadata(**meta_json)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.error(f"Failed to parse metadata: {e}")
        return None

    def send_keys(self, text: str, enter: bool = True, _internal: bool = False) -> None:
        """Send text to the terminal.

        Args:
            text: Text to send
            enter: Whether to add newline
            _internal: Internal flag for system commands (don't track as user command)

        Raises:
            RuntimeError: If terminal process is not running
        """
        # Validate process state
        if not self.process or self.process.poll() is not None:
            error_msg = "Cannot send keys: terminal process is not running"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Check if this is a special key (like C-c or Ctrl+C)
        is_special_key = self._is_special_key(text)

        # Clear old output buffer when sending a new command (not for special keys)
        if not is_special_key and not _internal:
            self._get_buffered_output(clear=True)

        # For regular commands (not special keys or internal),
        # append PS1 marker with metadata
        if not is_special_key and text.strip() and not _internal:
            # Set command running flag
            self._command_running_event.set()

            # Build PowerShell metadata output command with proper escaping
            ps1_begin = self._escape_powershell_string(CMD_OUTPUT_PS1_BEGIN.strip())
            ps1_end = self._escape_powershell_string(CMD_OUTPUT_PS1_END.strip())
            metadata_cmd = (
                f"; Write-Host '{ps1_begin}'; "
                # Use $? to check success (True/False), convert to 0/1
                "$exit_code = if ($?) { "
                "if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } "
                "else { 0 } } else { 1 }; "
                "$py_path = (Get-Command python -ErrorAction "
                "SilentlyContinue | Select-Object -ExpandProperty Source); "
                "$meta = @{pid=$PID; exit_code=$exit_code; "
                "username=$env:USERNAME; "
                "hostname=$env:COMPUTERNAME; "
                "working_dir=(Get-Location).Path.Replace('\\', '/'); "
                "py_interpreter_path=if ($py_path) { $py_path } "
                "else { $null }}; "
                "Write-Host (ConvertTo-Json $meta -Compress); "
                f"Write-Host '{ps1_end}'"
            )
            text = text.rstrip() + metadata_cmd

        if enter and not text.endswith("\n"):
            text = text + "\n"
        self._write_to_stdin(text)

    def read_screen(self) -> str:
        """Read current terminal output without clearing buffer.

        This allows TerminalSession to poll the output multiple times
        until it detects the PS1 prompt marker.

        Returns:
            Current buffered output
        """
        return self._get_buffered_output(clear=False)

    def clear_screen(self) -> None:
        """Clear the terminal screen."""
        self.send_keys("Clear-Host", enter=True, _internal=True)
        time.sleep(SCREEN_CLEAR_DELAY)
        self._get_buffered_output()  # Clear buffer
        # Reset command running flag since screen is cleared after command completion
        self._command_running_event.clear()

    def interrupt(self) -> bool:
        """Send interrupt signal to the terminal.

        Returns:
            True if successful
        """
        if self.process and self.process.poll() is None:
            try:
                # Send Ctrl+C to PowerShell
                self.send_keys(CTRL_C, enter=False)
                self._command_running_event.clear()
                return True
            except Exception as e:
                logger.error(f"Failed to send interrupt: {e}")
                return False
        return False

    def is_running(self) -> bool:
        """Check if a command is currently running.

        Returns:
            True if command is running
        """
        if not self._initialized or not self.process:
            return False

        # Check if process is still alive
        if self.process.poll() is not None:
            self._command_running_event.clear()
            return False

        try:
            content = self.read_screen()
            # Check for completion marker (PS1_END)
            if CMD_OUTPUT_PS1_END.rstrip() in content:
                self._command_running_event.clear()
                return False
            # Return current state - empty buffer doesn't mean command isn't running
            # (command might be executing without output yet)
            return self._command_running_event.is_set()
        except OSError as e:
            logger.warning(f"Error reading screen in is_running: {e}")
            return self._command_running_event.is_set()
        except Exception as e:
            logger.error(f"Unexpected error in is_running: {e}")
            return self._command_running_event.is_set()

    def is_powershell(self) -> bool:
        """Check if this is a PowerShell terminal.

        Returns:
            True (this is always PowerShell on Windows)
        """
        return True

    def close(self) -> None:
        """Close the terminal session."""
        if self._closed:
            return

        self._stop_reader = True

        # Close pipes to unblock reader thread
        if self.process:
            try:
                if self.process.stdin:
                    self.process.stdin.close()
            except (OSError, ValueError) as e:
                logger.debug(f"Error closing stdin: {e}")
            except Exception as e:
                logger.error(f"Unexpected error closing stdin: {e}")

            try:
                if self.process.stdout:
                    self.process.stdout.close()
            except (OSError, ValueError) as e:
                logger.debug(f"Error closing stdout: {e}")
            except Exception as e:
                logger.error(f"Unexpected error closing stdout: {e}")

        # Now join the reader thread
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=READER_THREAD_TIMEOUT)
            if self.reader_thread.is_alive():
                logger.warning("Reader thread did not terminate within timeout")

        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.warning("Process did not terminate, forcing kill")
                self.process.kill()
            except Exception as e:
                logger.error(f"Error terminating process: {e}")
            finally:
                self.process = None

        self._closed = True

    def __enter__(self):
        """Context manager entry."""
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False

    def __del__(self):
        """Cleanup on deletion."""
        try:
            self.close()
        except Exception:
            # Suppress errors during interpreter shutdown
            pass
