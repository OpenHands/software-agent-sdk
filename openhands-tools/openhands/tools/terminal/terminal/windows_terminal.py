"""PowerShell-backed terminal backend for Windows.

Design notes (see ``windows_console`` for the Win32 side):

* The command-completion sentinel is emitted by PowerShell's ``prompt``
  function, exactly like the bash backends use ``PS1``.  A sentinel that is
  appended to the command line itself is lost whenever the line does not run
  to completion (Ctrl+C, ``throw``, ``-ErrorAction Stop``,
  ``$ErrorActionPreference = 'Stop'``), which left the session believing the
  command was still running forever.  A prompt-level sentinel reappears
  whenever the shell is ready for input, no matter how the previous command
  ended.
* The shell gets a private hidden console (``CREATE_NO_WINDOW``) and is
  **not** created with ``CREATE_NEW_PROCESS_GROUP``: that flag disables Ctrl+C
  for the new process and everything it spawns.  Ctrl+C handling is also
  re-enabled from inside the shell so it works regardless of what the agent
  process itself inherited.
* Interrupts escalate with bounded waits: console Ctrl+C -> terminate the
  descendant process tree -> Ctrl+C again.  If the shell still does not come
  back to a prompt it is declared unresponsive and the session controller
  recreates it, so one bad command can never poison the session.
* The shell and its descendants live in a job object, which gives an exact
  process-tree view and an atomic teardown without WMI queries, and which
  never kills the shell's own console host (that leaves PowerShell alive but
  wedged).
"""

import codecs
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping

from openhands.sdk.logger import get_logger
from openhands.tools.terminal.constants import (
    CMD_OUTPUT_PS1_BEGIN,
    CMD_OUTPUT_PS1_END,
)
from openhands.tools.terminal.env import (
    build_terminal_env,
    normalize_terminal_env,
)
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal import windows_console
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    parse_ctrl_key,
)


logger = get_logger(__name__)

_READ_CHUNK_SIZE = 4096
_READER_THREAD_TIMEOUT_SECONDS = 1.0
_PROMPT_POLL_INTERVAL_SECONDS = 0.05
# Upper bound for the first prompt after start-up.  PowerShell 5.1 can take a
# few seconds on a cold or loaded machine; this is a cap, not a wait.
_STARTUP_PROMPT_TIMEOUT_SECONDS = 30.0
# How long each interrupt escalation step waits for the prompt to come back.
_INTERRUPT_PROMPT_WAIT_SECONDS = 3.0
_CTRL_C_HELPER_TIMEOUT_SECONDS = 5.0
_PROCESS_EXIT_WAIT_SECONDS = 5.0
# Screen buffer budget in characters (the tmux backend keeps 10k lines).
_OUTPUT_BUFFER_MAX_CHARS = 1_000_000

_PS1_BEGIN_MARKER = CMD_OUTPUT_PS1_BEGIN.strip()
_PS1_END_MARKER = CMD_OUTPUT_PS1_END.strip()
# What the prompt function writes at the end of every metadata block.  The
# leading newline is what distinguishes a real prompt from the same text
# appearing inside an echoed command line.
_PROMPT_END_SEQUENCE = "\n" + _PS1_END_MARKER

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


def _ps_single_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _ps_split_literal(text: str) -> str:
    """Return a PowerShell expression that evaluates to *text*.

    PowerShell echoes every line it reads from a pipe, so the init script must
    not contain the marker strings verbatim or the echo itself would look like
    a prompt.  Splitting the literal in two keeps the markers out of the echo.
    """
    half = len(text) // 2
    return f"({_ps_single_quote(text[:half])} + {_ps_single_quote(text[half:])})"


def build_powershell_init_script() -> str:
    """Return the statements sent to a fresh PowerShell session.

    Each statement is a complete single line so PowerShell never waits at the
    ``>>`` continuation prompt while reading them from the stdin pipe.
    """
    # 1. Re-enable Ctrl+C for this process (and, by inheritance, everything it
    #    spawns).  The flag is inherited from the parent and is set by
    #    CREATE_NEW_PROCESS_GROUP, so we cannot rely on what we got.
    enable_ctrl_c = (
        "try { Add-Type -Namespace OpenHandsTerminal -Name ConsoleCtrl "
        '-MemberDefinition \'[DllImport("kernel32.dll", SetLastError = true)] '
        "public static extern bool SetConsoleCtrlHandler(System.IntPtr handler, "
        "bool add);' -ErrorAction Stop; "
        "[void][OpenHandsTerminal.ConsoleCtrl]::SetConsoleCtrlHandler("
        "[System.IntPtr]::Zero, $false) } "
        'catch { Write-Host "[openhands] Ctrl+C could not be enabled: $_" }'
    )
    # 2. UTF-8 in and out: the reader decodes UTF-8 and native tools emit it.
    utf8 = (
        "try { $oh_utf8 = New-Object System.Text.UTF8Encoding($false); "
        "[Console]::OutputEncoding = $oh_utf8; $global:OutputEncoding = $oh_utf8 } "
        "catch { }"
    )
    # 3. The prompt function emits the metadata block.  `$?` must be read
    #    before anything else runs.  The END marker is the prompt string
    #    itself, so the screen ends with it whenever the shell is idle.
    begin = _ps_split_literal(_PS1_BEGIN_MARKER)
    end = f'({_ps_split_literal(_PS1_END_MARKER)} + "`n")'
    prompt = (
        "function global:prompt { "
        "$oh_ok = $?; "
        "$oh_ec = $global:LASTEXITCODE; "
        "$global:LASTEXITCODE = $null; "
        "$oh_code = if ($null -ne $oh_ec) { $oh_ec } elseif ($oh_ok) { 0 } else { 1 }; "
        "try { $oh_cwd = (Get-Location).Path.Replace('\\', '/') } "
        "catch { $oh_cwd = $null }; "
        "try { $oh_py = (Get-Command python -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty Source) } catch { $oh_py = $null }; "
        "$oh_meta = @{ pid = $PID; exit_code = $oh_code; username = $env:USERNAME; "
        "hostname = $env:COMPUTERNAME; working_dir = $oh_cwd; "
        "py_interpreter_path = $oh_py }; "
        "try { $oh_json = ConvertTo-Json $oh_meta -Compress } "
        "catch { $oh_json = '{\"exit_code\":' + $oh_code + '}' }; "
        f'Write-Host ("`n" + {begin} + "`n" + $oh_json); '
        f"return {end} "
        "}"
    )
    return "\n".join([enable_ctrl_c, utf8, prompt]) + "\n"


class WindowsTerminal(TerminalInterface):
    """Persistent PowerShell session for Windows terminal execution."""

    process: subprocess.Popen[bytes] | None
    output_buffer: deque[str]
    output_lock: threading.Lock
    reader_thread: threading.Thread | None
    shell_path: str
    _stop_reader: threading.Event
    _decoder: codecs.IncrementalDecoder

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        shell_path: str = "powershell.exe",
        env: Mapping[str, str] | None = None,
    ):
        super().__init__(work_dir, username)
        self.process = None
        self.output_buffer = deque()
        self.output_lock = threading.Lock()
        self.reader_thread = None
        self.shell_path = shell_path
        self._env = normalize_terminal_env(env)
        self._stop_reader = threading.Event()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer_chars = 0
        # Monotonic count of metadata blocks (prompts) seen on the stream, and
        # the value it had when the current command was submitted.  A command
        # is running exactly while ``_prompt_seq <= _seq_at_send``.
        self._prompt_seq = 0
        self._seq_at_send = 0
        self._marker_carry = ""
        self._job: windows_console.WindowsJob | None = None
        # The shell and its own console host: never terminated by
        # ``_kill_descendants`` (killing conhost wedges PowerShell).
        self._protected_pids: set[int] = set()
        self._wedged = False
        self._interrupt_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
            # A private hidden console for the shell and its children.  Do NOT
            # add CREATE_NEW_PROCESS_GROUP: it disables Ctrl+C for the tree.
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        env = build_terminal_env(self._env)
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
        self._attach_job()

        self._stop_reader.clear()
        self.reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self.reader_thread.start()
        self._initialized = True

        self._write_to_stdin(build_powershell_init_script())
        if not self._wait_for_prompt_after(0, _STARTUP_PROMPT_TIMEOUT_SECONDS):
            logger.warning(
                "PowerShell did not print its first prompt within %.0fs; "
                "continuing with a seeded metadata block",
                _STARTUP_PROMPT_TIMEOUT_SECONDS,
            )
        self._record_protected_pids()
        self.clear_screen()
        logger.debug("Windows terminal initialized with work dir: %s", self.work_dir)

    def _attach_job(self) -> None:
        if platform.system() != "Windows" or self.process is None:
            return
        handle = getattr(self.process, "_handle", None)
        if handle is None:
            return
        job = windows_console.WindowsJob()
        if job.active and job.assign(int(handle)):
            self._job = job
        else:
            job.close()
            logger.warning(
                "Could not place PowerShell in a job object; falling back to "
                "parent-pid process-tree enumeration"
            )

    def _record_protected_pids(self) -> None:
        if self.process is None:
            return
        protected = {self.process.pid}
        if self._job is not None:
            protected.update(self._job.pids() or [])
        else:
            for pid, ppid, name in windows_console.snapshot_processes():
                if ppid == self.process.pid and name.lower() == "conhost.exe":
                    protected.add(pid)
        self._protected_pids = protected

    def close(self) -> None:
        """Stop the PowerShell process tree and the background reader."""
        if self._closed:
            return
        self._closed = True
        self._stop_reader.set()

        process = self.process
        if process is not None:
            if self._job is not None and process.poll() is None:
                self._job.terminate()
            elif process.poll() is None:
                self._kill_descendants()
                try:
                    process.kill()
                except OSError as exc:
                    logger.debug("Error killing PowerShell process: %s", exc)
            for stream in (process.stdin, process.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError) as exc:
                    logger.debug("Error closing PowerShell pipe: %s", exc)

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=_READER_THREAD_TIMEOUT_SECONDS)

        if process is not None:
            try:
                process.wait(timeout=_PROCESS_EXIT_WAIT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("PowerShell process did not exit after termination")
            except Exception as exc:
                logger.debug("Error waiting for PowerShell process: %s", exc)
            self.process = None

        if self._job is not None:
            self._job.close()
            self._job = None

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

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
        command = stripped_text if stripped_text else text
        with self.output_lock:
            self._seq_at_send = self._prompt_seq

        if enter:
            if "\n" in stripped_text or "\r" in stripped_text:
                # PowerShell holds a multiline statement at the ">>" continuation
                # prompt until a blank line is received, so multiline input needs a
                # trailing empty line to execute.
                command += "\n\n"
            elif not command.endswith("\n"):
                command += "\n"
        self._write_to_stdin(command)

    def _write_to_stdin(self, text: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("PowerShell stdin is not available")
        try:
            self.process.stdin.write(text.encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            logger.error("Failed to write to PowerShell stdin: %s", exc)
            raise RuntimeError("Failed to write to PowerShell session") from exc

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

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
                    self._append_output(decoded)
            except (ValueError, OSError) as exc:
                logger.debug("PowerShell output reading stopped: %s", exc)
                break
            except Exception as exc:
                logger.error("Error reading PowerShell output: %s", exc)
                break

        try:
            final = self._decoder.decode(b"", final=True)
            if final:
                self._append_output(final)
        except Exception as exc:
            logger.debug("Error flushing PowerShell decoder: %s", exc)

    def _append_output(self, decoded: str) -> None:
        with self.output_lock:
            self.output_buffer.append(decoded)
            self._buffer_chars += len(decoded)
            # Count prompts as they stream by; a marker may straddle two chunks.
            joined = self._marker_carry + decoded
            seen = joined.count(_PROMPT_END_SEQUENCE)
            if seen:
                self._prompt_seq += seen
            self._marker_carry = joined[-(len(_PROMPT_END_SEQUENCE) - 1) :]
            while (
                self._buffer_chars > _OUTPUT_BUFFER_MAX_CHARS
                and len(self.output_buffer) > 1
            ):
                self._buffer_chars -= len(self.output_buffer.popleft())

    def _get_buffered_output(self, clear: bool) -> str:
        with self.output_lock:
            output = "".join(self.output_buffer)
            if clear:
                self.output_buffer.clear()
                self._buffer_chars = 0
            return output

    def read_screen(self) -> str:
        """Return the accumulated visible PowerShell output."""
        return self._get_buffered_output(clear=False)

    def clear_screen(self) -> None:
        """Drop everything except the latest metadata block."""
        if self.process is None or self.process.poll() is not None:
            return
        if not self._preserve_latest_metadata_block():
            self._seed_metadata_prompt()

    def _preserve_latest_metadata_block(self) -> bool:
        """Keep only the last well-formed metadata block in the buffer."""
        with self.output_lock:
            output = "".join(self.output_buffer)
            matches = CmdOutputMetadata.matches_ps1_metadata(output)
            if not matches:
                self.output_buffer.clear()
                self._buffer_chars = 0
                return False

            block = output[matches[-1].start() : matches[-1].end()] + "\n"
            self.output_buffer.clear()
            self.output_buffer.append(block)
            self._buffer_chars = len(block)
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
            f"{_PS1_BEGIN_MARKER}\n"
            f"{json.dumps(metadata, separators=(',', ':'))}\n"
            f"{_PS1_END_MARKER}\n"
        )
        with self.output_lock:
            self.output_buffer.clear()
            self.output_buffer.append(prompt)
            self._buffer_chars = len(prompt)

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def _prompt_count(self) -> int:
        with self.output_lock:
            return self._prompt_seq

    def _wait_for_prompt_after(self, seq: int, timeout: float) -> bool:
        """Block until a prompt newer than *seq* has been printed."""
        deadline = time.monotonic() + timeout
        while True:
            if self._prompt_count() > seq:
                return True
            if self.process is None or self.process.poll() is not None:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(_PROMPT_POLL_INTERVAL_SECONDS)

    def is_running(self) -> bool:
        """Return whether a command is still running in the PowerShell session."""
        if not self._initialized or self.process is None:
            return False
        if self.process.poll() is not None:
            return False
        with self.output_lock:
            return self._prompt_seq <= self._seq_at_send

    def is_alive(self) -> bool:
        """Return whether the shell can still accept commands."""
        return (
            self._initialized
            and not self._closed
            and not self._wedged
            and self.process is not None
            and self.process.poll() is None
        )

    def is_powershell(self) -> bool:
        return True

    # ------------------------------------------------------------------
    # Interrupt
    # ------------------------------------------------------------------

    def _kill_descendants(self) -> bool:
        """Terminate every process under the shell except the shell and its conhost."""  # noqa: E501
        if platform.system() != "Windows" or self.process is None:
            return False
        pids: list[int] | None = None
        if self._job is not None:
            pids = self._job.pids()
        if pids is None:
            pids = windows_console.descendant_pids(self.process.pid)
        victims = [
            pid
            for pid in pids
            if pid != self.process.pid and pid not in self._protected_pids
        ]
        killed = False
        for pid in victims:
            if windows_console.terminate_pid(pid):
                killed = True
        logger.debug("Terminated %d descendant process(es): %s", len(victims), victims)
        return killed

    def interrupt(self) -> bool:
        """Interrupt the running command and wait for the prompt to come back.

        Two things must both happen: the shell must return to a prompt, and
        the command's process subtree must be gone (a child launched with
        ``Start-Process`` runs on its own console, so Ctrl+C returns the
        prompt but leaves the child alive — the tree kill is what stops it,
        mirroring ``killpg(SIGINT)`` on POSIX).  So Ctrl+C and a descendant
        sweep are always paired, and the step escalates on failure.  When the
        shell still will not come back it is marked unresponsive
        (``is_alive()`` -> ``False``) so the session controller recreates it
        rather than retrying forever.
        """
        if self.process is None or self.process.poll() is not None:
            return False

        with self._interrupt_lock:
            running = self.is_running()
            seq = self._prompt_count()

            delivered = windows_console.send_console_ctrl_c(
                self.process.pid, timeout=_CTRL_C_HELPER_TIMEOUT_SECONDS
            )
            if not delivered:
                logger.debug("Console Ctrl+C could not be delivered to PowerShell")
            # Give Ctrl+C a moment to unwind the pipeline before enumerating the
            # tree, so a child that reacts to Ctrl+C is not double-reported.
            prompt_back = self._wait_for_prompt_after(
                seq, _INTERRUPT_PROMPT_WAIT_SECONDS if running else 0.0
            )
            # Always take the subtree down: Ctrl+C does not reach children on
            # their own console (Start-Process) or children that ignore it.
            self._kill_descendants()

            if not running:
                # Nothing was running (idle Ctrl+C): make it idempotent.
                return True
            if prompt_back or self._wait_for_prompt_after(
                seq, _INTERRUPT_PROMPT_WAIT_SECONDS
            ):
                return True

            # Prompt still hasn't returned: try Ctrl+C once more after the tree
            # has been torn down, then give up and let the session recreate us.
            windows_console.send_console_ctrl_c(
                self.process.pid, timeout=_CTRL_C_HELPER_TIMEOUT_SECONDS
            )
            if self._wait_for_prompt_after(seq, _INTERRUPT_PROMPT_WAIT_SECONDS):
                return True

            if self.process.poll() is not None:
                return False
            logger.warning(
                "PowerShell did not return to a prompt after Ctrl+C and "
                "process-tree termination; marking the terminal unresponsive"
            )
            self._wedged = True
            return False

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
