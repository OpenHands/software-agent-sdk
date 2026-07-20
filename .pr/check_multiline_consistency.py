"""Evidence for the review question on PR #4155.

Question raised: does the unix terminal reject multi-line commands, and is the
Windows terminal consistent with it?

What this script establishes:

1. The guard rejects multiple *statements*, not multi-line commands. It fires on
   ``len(split_bash_commands(command)) > 1``, so a single statement spanning
   several lines returns one element and runs normally.
2. Newlines separate statements; ``&&`` and ``;`` do not -- which is exactly the
   guidance the tool description already gives the agent.
3. The Windows terminal cannot diverge from this. ``execute()`` has a single
   concrete implementation, ``TerminalSession.execute``; terminal backends
   (including ``WindowsTerminal``) implement ``TerminalInterface``, which has no
   ``execute`` at all, and nothing subclasses ``TerminalSession``.
4. The guard runs *before* any terminal I/O: a rejected command reaches
   ``send_keys()`` zero times. So the multiline PowerShell hang this PR fixes is
   downstream of the guard, not something the guard was ever going to catch.
5. On unix, multi-line commands really do execute with ``exit_code == 0``.

Every claim is asserted; the script exits non-zero if any check fails.

Run with::

    uv run python .pr/check_multiline_consistency.py

Sections 1-4 are pure and run on any platform, Windows included. Section 5
executes bash-syntax commands, so it is skipped off unix; the Windows execution
path is covered by the regression test added in this PR.
"""

import inspect
import logging
import platform
import sys
import tempfile

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import factory
from openhands.tools.terminal.terminal.factory import create_terminal_session
from openhands.tools.terminal.terminal.interface import (
    TerminalInterface,
    TerminalSessionBase,
)
from openhands.tools.terminal.terminal.terminal_session import TerminalSession
from openhands.tools.terminal.terminal.windows_terminal import WindowsTerminal
from openhands.tools.terminal.utils.command import split_bash_commands


_FAILURES: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    """Record a pass/fail check and print it."""
    print(
        f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else "")
    )
    if not ok:
        _FAILURES.append(label)


# --------------------------------------------------------------------------
# 1. What the guard actually classifies
# --------------------------------------------------------------------------
# (label, command, expected number of statements)
CASES = [
    ("PR repro (PowerShell), newline in string", 'Write-Output "a\nb"', 1),
    ("unix echo, newline in string", 'echo -e "hello\nworld"', 1),
    ("unix for-loop spanning 3 lines", "for i in 1 2; do\n  echo $i\ndone", 1),
    ("unix backslash continuation", 'echo \\\n -e "foo"', 1),
    ("heredoc spanning 4 lines", "cat <<EOF\nline1\nline2\nEOF", 1),
    ("TWO statements, newline-separated", "ls -l\necho hi", 2),
]

# Same two commands, three separators: only the newline splits them.
SEPARATORS = [("&&", 1), (";", 1), ("\n", 2)]


def section_guard() -> None:
    print("== 1. guard input: split_bash_commands() ==")
    print("     multi-LINE commands stay one statement; only extra STATEMENTS split\n")
    for label, cmd, expected in CASES:
        n = len(split_bash_commands(cmd))
        verdict = "REJECTED" if n > 1 else "runs"
        check(
            n == expected,
            f"{n} statement(s), {verdict:8}  {label}",
            "" if n == expected else f"expected {expected}",
        )


def section_separators() -> None:
    print("\n== 2. what separates two statements ==")
    print("     'ls -l <sep> echo hi' -- newline splits, && and ; do not\n")
    for sep, expected in SEPARATORS:
        n = len(split_bash_commands(f"ls -l{sep}echo hi"))
        shown = repr(sep)
        check(
            n == expected,
            f"sep={shown:6} -> {n} statement(s), {'REJECTED' if n > 1 else 'runs'}",
            "" if n == expected else f"expected {expected}",
        )


# --------------------------------------------------------------------------
# 3. The Windows path structurally cannot bypass the guard
# --------------------------------------------------------------------------
def section_windows_shares_guard() -> None:
    print("\n== 3. can the Windows terminal bypass the guard? ==")
    print("     no: there is exactly one execute() implementation\n")

    check(
        "execute" in TerminalSession.__dict__,
        "TerminalSession implements execute() -- this is where the guard lives",
    )
    check(
        not hasattr(TerminalInterface, "execute"),
        "TerminalInterface (backend contract) has no execute() at all",
    )
    check(
        "execute" not in WindowsTerminal.__dict__
        and issubclass(WindowsTerminal, TerminalInterface),
        "WindowsTerminal is a backend and defines no execute() of its own",
    )
    check(
        not TerminalSession.__subclasses__(),
        "nothing subclasses TerminalSession, so execute() cannot be overridden",
        f"subclasses={[c.__name__ for c in TerminalSession.__subclasses__()]}",
    )
    ret = inspect.signature(factory._create_windows_terminal).return_annotation
    check(
        ret is TerminalSession or "TerminalSession" in str(ret),
        "_create_windows_terminal() returns a TerminalSession",
        f"-> {ret if isinstance(ret, str) else getattr(ret, '__name__', ret)}",
    )
    # Sanity: the session ABC does declare execute, so the wrapper is the only
    # thing that can answer it.
    check(
        "execute" in TerminalSessionBase.__abstractmethods__,
        "execute() is declared abstract on TerminalSessionBase (session contract)",
    )


# --------------------------------------------------------------------------
# 4. The guard runs before any terminal I/O
# --------------------------------------------------------------------------
class RecordingTerminal(TerminalInterface):
    """Backend that records what would be written, without a real shell."""

    def __init__(self, work_dir: str) -> None:
        super().__init__(work_dir)
        self.sent: list[tuple[str, bool]] = []

    def initialize(self) -> None:
        self._initialized = True

    def close(self) -> None:
        self._closed = True

    def is_running(self) -> bool:
        return False

    def send_keys(self, text: str, enter: bool = True) -> None:
        self.sent.append((text, enter))

    def read_screen(self) -> str:
        return ""

    def clear_screen(self) -> None:
        return None

    def interrupt(self) -> bool:
        return True


def _sent_for(command: str, timeout: float) -> tuple[int, bool]:
    """Run *command* against a recording backend; return (writes, is_error)."""
    # The stub has no screen content, so the session logs an expected
    # "no PS1 metadata" warning. Silence just that logger for this section.
    session_logger = logging.getLogger(
        "openhands.tools.terminal.terminal.terminal_session"
    )
    previous_level = session_logger.level
    session_logger.setLevel(logging.ERROR)
    try:
        with tempfile.TemporaryDirectory() as d:
            terminal = RecordingTerminal(d)
            session = TerminalSession(terminal, no_change_timeout_seconds=1)
            session.initialize()
            try:
                obs = session.execute(TerminalAction(command=command, timeout=timeout))
                return len(terminal.sent), obs.is_error
            finally:
                session.close()
    finally:
        session_logger.setLevel(previous_level)


def section_guard_precedes_io() -> None:
    print("\n== 4. does the guard run before the terminal is touched? ==")
    print("     yes: a rejected command never reaches send_keys()\n")

    writes, is_error = _sent_for("ls -l\necho hi", timeout=1)
    ok = is_error and writes == 0
    check(
        ok,
        f"2 statements -> rejected, send_keys() called {writes}x",
        "" if ok else "expected rejection with 0 writes",
    )

    writes, _ = _sent_for('echo -e "hello\nworld"', timeout=1)
    check(
        writes > 0,
        f"multi-line, 1 statement -> accepted, send_keys() called {writes}x",
        "" if writes > 0 else "expected >0 writes",
    )
    print("     (so the multiline PowerShell hang this PR fixes is downstream of")
    print("      the guard -- send_keys() is reached, then PowerShell sits at '>>')")


# --------------------------------------------------------------------------
# 5. Real execution on a unix host
# --------------------------------------------------------------------------
def section_execute() -> None:
    print("\n== 5. actually executing on this unix host ==")
    if platform.system() == "Windows":
        print("     skipped: bash-syntax commands; see the PR's Windows test\n")
        return
    print("     multi-line commands run; only the extra statement is refused\n")

    expectations = [
        ('echo -e "hello\nworld"', 0, False, "multi-line, 1 statement"),
        ("for i in 1 2; do\n  echo $i\ndone", 0, False, "for-loop over 3 lines"),
        ("ls -l\necho hi", -1, True, "TWO statements"),
    ]
    with tempfile.TemporaryDirectory() as d:
        session = create_terminal_session(work_dir=d)
        session.initialize()
        try:
            for cmd, want_code, want_error, label in expectations:
                obs = session.execute(TerminalAction(command=cmd))
                got = obs.metadata.exit_code
                ok = got == want_code and obs.is_error is want_error
                check(
                    ok,
                    f"exit_code={got:<3} is_error={obs.is_error!s:5}  {label}",
                    "" if ok else f"expected exit_code={want_code}",
                )
                if obs.is_error:
                    print(f"         -> {obs.text.splitlines()[0]}")
        finally:
            session.close()


def main() -> int:
    section_guard()
    section_separators()
    section_windows_shares_guard()
    section_guard_precedes_io()
    section_execute()

    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}): " + "; ".join(_FAILURES))
        return 1
    print("All checks passed.")
    print("Conclusion: the guard rejects multiple STATEMENTS, not multi-line")
    print("commands; unix and Windows share that one guard; this PR fixes a")
    print("send_keys() bug downstream of it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
