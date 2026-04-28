"""PowerShell-backed terminal backend for Windows."""

import codecs
import json
import os
import platform
import shutil
import signal
import subprocess
import threading
import time
from collections import deque

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_PS1_BEGIN,
    CMD_OUTPUT_PS1_END,
    HISTORY_LIMIT,
)
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    parse_ctrl_key,
)


logger = get_logger(__name__)

_READ_CHUNK_SIZE = 1024
_READER_THREAD_TIMEOUT_SECONDS = 1.0
_SCREEN_CLEAR_DELAY_SECONDS = 0.2
_SETUP_DELAY_SECONDS = 0.5
_SETUP_POLL_INTERVAL_SECONDS = 0.05
_MAX_SETUP_WAIT_SECONDS = 2.0

_WINDOWS_SPECIALS: dict[str, str] = {
    "ENTER": "\n",
    "TAB": "\t",
    "BS": "\b",
    "ESC": "\x1b",
    "UP": "\x1b[A",
    "DOWN": "\x1b[B",
    "LEFT": "\x1b[D",
    "RIGHT": "\x1b[C",
    "HOME": "\x1b[H",
    "END": "\x1b[F",
    "PGUP": "\x1b[5~",
    "PGDN": "\x1b[6~",
    "C-L": "\x0c",
    "C-D": "\x04",
    "C-C": "\x03",
}


class WindowsTerminal(TerminalInterface):
    """Persistent PowerShell session for Windows terminal execution."""

    process: subprocess.Popen[bytes] | None
    output_buffer: deque[str]
    output_lock: threading.Lock
    reader_thread: threading.Thread | None
    shell_path: str
    _command_running_event: threading.Event
    _stop_reader: threading.Event
    _decoder: codecs.IncrementalDecoder

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        shell_path: str = "powershell.exe",
    ):
        super().__init__(work_dir, username)
        self.process = None
        self.output_buffer = deque(maxlen=HISTORY_LIMIT)
        self.output_lock = threading.Lock()
        self.reader_thread = None
        self.shell_path = shell_path
        self._command_running_event = threading.Event()
        self._stop_reader = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def initialize(self) -> None:
        """Start a persistent PowerShell process and prepare prompt metadata."""
        if self._initialized:
            return

        startupinfo = None
        creationflags = 0
        if platform.system() == "Windows":
            startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
            if startupinfo_cls is not None:
                startupinfo = startupinfo_cls()
                startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)

        env = sanitized_env()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        self.process = subprocess.Popen(
            [self.shell_path, "-NoLogo", "-NoProfile"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=self.work_dir,
            env=env,
            text=False,
            bufsize=0,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

        self._stop_reader.clear()
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        self._initialized = True

        self._wait_for_startup_output()
        self.clear_screen()
        logger.debug("Windows terminal initialized with work dir: %s", self.work_dir)

    def _wait_for_startup_output(self) -> None:
        deadline = time.time() + _MAX_SETUP_WAIT_SECONDS
        while time.time() < deadline:
            time.sleep(_SETUP_POLL_INTERVAL_SECONDS)
            with self.output_lock:
                if self.output_buffer:
                    break
        time.sleep(_SETUP_DELAY_SECONDS)
        self._get_buffered_output(clear=True)

    def _preserve_latest_metadata_block(self) -> bool:
        ps1_begin = CMD_OUTPUT_PS1_BEGIN.strip()
        ps1_end = CMD_OUTPUT_PS1_END.strip()
        with self.output_lock:
            output = "".join(self.output_buffer)
            start_index = output.rfind(ps1_begin)
            end_index = output.rfind(ps1_end)
            if start_index == -1 or end_index == -1 or end_index < start_index:
                self.output_buffer.clear()
                return False

            end_index += len(ps1_end)
            self.output_buffer.clear()
            self.output_buffer.append(output[start_index:end_index] + "\n")
            return True

    def _seed_metadata_prompt(self) -> None:
        env = os.environ
        metadata = {
            "pid": self.process.pid if self.process is not None else -1,
            "exit_code": 0,
            "username": env.get("USERNAME"),
            "hostname": env.get("COMPUTERNAME"),
            "working_dir": os.path.realpath(self.work_dir).replace("\\", "/"),
            "py_interpreter_path": shutil.which("python"),
        }
        prompt = (
            f"{CMD_OUTPUT_PS1_BEGIN.strip()}\n"
            f"{json.dumps(metadata, separators=(',', ':'))}\n"
            f"{CMD_OUTPUT_PS1_END.strip()}\n"
        )
        with self.output_lock:
            self.output_buffer.clear()
            self.output_buffer.append(prompt)

    def close(self) -> None:
        """Stop the PowerShell process and background reader."""
        if self._closed:
            return

        self._stop_reader.set()

        if self.process is not None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
            except (OSError, ValueError) as exc:
                logger.debug("Error closing PowerShell stdin: %s", exc)

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=_READER_THREAD_TIMEOUT_SECONDS)

        if self.process is not None:
            try:
                if self.process.stdout is not None:
                    self.process.stdout.close()
            except (OSError, ValueError) as exc:
                logger.debug("Error closing PowerShell stdout: %s", exc)
            try:
                self.process.terminate()
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                logger.warning("PowerShell process did not terminate, forcing kill")
                self.process.kill()
            except Exception as exc:
                logger.debug("Error terminating PowerShell process: %s", exc)
            finally:
                self.process = None

        self._closed = True

    def send_keys(self, text: str, enter: bool = True) -> None:
        """Send text or supported control sequences to the PowerShell session."""
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Cannot send keys: PowerShell process is not running")

        upper = text.strip().upper()
        ctrl = parse_ctrl_key(text)
        if upper == "C-C" or ctrl == "C-c":
            self.interrupt()
            return
        if upper in _WINDOWS_SPECIALS:
            self._write_to_stdin(_WINDOWS_SPECIALS[upper])
            return
        if ctrl is not None:
            ctrl_char = chr(ord(ctrl[-1]) - ord("a") + 1)
            self._write_to_stdin(ctrl_char)
            return

        stripped_text = text.rstrip()
        if stripped_text:
            self._command_running_event.set()
            command = f"{stripped_text}; {self._metadata_suffix()}"
        else:
            command = text

        if enter and not command.endswith("\n"):
            command += "\n"
        self._write_to_stdin(command)

    def _metadata_suffix(self) -> str:
        ps1_begin = self._escape_single_quoted(CMD_OUTPUT_PS1_BEGIN.strip())
        ps1_end = self._escape_single_quoted(CMD_OUTPUT_PS1_END.strip())
        commands = [
            "$oh1 = $?",
            "$oh2 = $LASTEXITCODE",
            f"Write-Host '{ps1_begin}'",
            (
                "$exit_code = if ($null -ne $oh2) { "
                "$oh2 "
                "} elseif ($oh1) { 0 } else { 1 }"
            ),
            (
                "$py_path = (Get-Command python -ErrorAction SilentlyContinue | "
                "Select-Object -ExpandProperty Source)"
            ),
            (
                "$meta = @{"
                "pid=$PID; "
                "exit_code=$exit_code; "
                "username=$env:USERNAME; "
                "hostname=$env:COMPUTERNAME; "
                "working_dir=(Get-Location).Path.Replace('\\', '/'); "
                "py_interpreter_path=if ($py_path) { $py_path } else { $null }"
                "}"
            ),
            "Write-Host (ConvertTo-Json $meta -Compress)",
            f"Write-Host '{ps1_end}'",
            "$global:LASTEXITCODE = $null",
        ]
        return "; ".join(commands)

    @staticmethod
    def _escape_single_quoted(text: str) -> str:
        return text.replace("'", "''")

    def _write_to_stdin(self, text: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("PowerShell stdin is not available")
        try:
            self.process.stdin.write(text.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.error("Failed to write to PowerShell stdin: %s", exc)
            raise RuntimeError("Failed to write to PowerShell session") from exc

    def _read_output(self) -> None:
        if self.process is None or self.process.stdout is None:
            return

        stdout = self.process.stdout
        while not self._stop_reader.is_set():
            try:
                chunk = stdout.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                decoded = self._decoder.decode(chunk, final=False)
                if decoded:
                    with self.output_lock:
                        self.output_buffer.append(decoded)
            except (ValueError, OSError) as exc:
                logger.debug("PowerShell output reading stopped: %s", exc)
                break
            except Exception as exc:
                logger.error("Error reading PowerShell output: %s", exc)
                break

        try:
            final = self._decoder.decode(b"", final=True)
            if final:
                with self.output_lock:
                    self.output_buffer.append(final)
        except Exception as exc:
            logger.debug("Error flushing PowerShell decoder: %s", exc)

    def _get_buffered_output(self, clear: bool) -> str:
        with self.output_lock:
            output = "".join(self.output_buffer)
            if clear:
                self.output_buffer.clear()
            return output

    def read_screen(self) -> str:
        """Return the accumulated visible PowerShell output."""
        return self._get_buffered_output(clear=False)

    def clear_screen(self) -> None:
        """Clear the visible screen and reset buffered output."""
        if self.process is None or self.process.poll() is not None:
            return

        if not self._preserve_latest_metadata_block():
            self._seed_metadata_prompt()
        time.sleep(_SCREEN_CLEAR_DELAY_SECONDS)
        self._command_running_event.clear()

    def interrupt(self) -> bool:
        """Interrupt the active command if the process is still alive."""
        if self.process is None or self.process.poll() is not None:
            return False

        sent_ctrl_break = False
        ctrl_break_event = getattr(signal, "CTRL_BREAK_EVENT", None)
        if platform.system() == "Windows" and ctrl_break_event is not None:
            try:
                self.process.send_signal(ctrl_break_event)
                sent_ctrl_break = True
            except Exception as exc:
                logger.debug("Failed to send CTRL_BREAK_EVENT: %s", exc)

        if not sent_ctrl_break:
            try:
                self._write_to_stdin(_WINDOWS_SPECIALS["C-C"])
            except RuntimeError as exc:
                logger.debug("Failed to write Ctrl+C to PowerShell stdin: %s", exc)
                return False

        self._command_running_event.clear()
        return True

    def is_running(self) -> bool:
        """Return whether a command is still running in the PowerShell session."""
        if not self._initialized or self.process is None:
            return False
        if self.process.poll() is not None:
            self._command_running_event.clear()
            return False

        content = self.read_screen()
        if CMD_OUTPUT_PS1_END.rstrip() in content:
            self._command_running_event.clear()
            return False
        return self._command_running_event.is_set()

    def is_powershell(self) -> bool:
        return True

    def __enter__(self) -> "WindowsTerminal":
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        self.close()
        return False

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
