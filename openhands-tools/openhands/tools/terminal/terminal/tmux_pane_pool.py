"""Pool of tmux panes for parallel terminal command execution.

Maintains a fixed-size pool of TmuxTerminal instances within a single
tmux session, enabling concurrent command execution across panes.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Final

import libtmux

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env
from openhands.tools.terminal.constants import (
    HISTORY_LIMIT,
    TMUX_SESSION_HEIGHT,
    TMUX_SESSION_WIDTH,
    TMUX_SOCKET_NAME,
)
from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


logger = get_logger(__name__)

DEFAULT_MAX_PANES: Final[int] = 4


def _safe_pane_id(terminal: PooledTmuxTerminal) -> str:
    try:
        return str(terminal.pane.pane_id)
    except Exception:
        return "<unavailable>"


class PooledTmuxTerminal(TmuxTerminal):
    """A TmuxTerminal variant used inside a pane pool.

    Overrides ``close()`` to only kill this terminal's window instead of
    the entire shared tmux session.  This is critical because
    ``TerminalSessionBase.__del__`` calls ``close()``, and GC of a cached
    ``TerminalSession`` wrapper would otherwise destroy the session that
    all other pool panes depend on.
    """

    def close(self) -> None:
        if not self._closed:
            with suppress(Exception):
                self.window.kill()
            self._closed = True


@dataclass(slots=True)
class PaneHandle:
    """Mutable handle to a checked-out pane, for use as a context manager target."""

    terminal: PooledTmuxTerminal


@dataclass(slots=True)
class TmuxPanePool:
    """Thread-safe pool of tmux panes for parallel terminal execution.

    Each pane is a fully configured TmuxTerminal sharing a single tmux
    session.  Callers check out a pane, run commands, and check it back
    in.  A semaphore limits concurrency to ``max_panes``.

    Usage:

        pool = TmuxPanePool("/workspace", max_panes=4)
        pool.initialize()

        terminal = pool.checkout()
        terminal.send_keys("echo hello")
        output = terminal.read_screen()
        pool.checkin(terminal)

        pool.close()
    """

    work_dir: str
    username: str | None = None
    max_panes: int = DEFAULT_MAX_PANES

    # tmux handles
    _server: libtmux.Server | None = field(default=None, init=False, repr=False)
    _session: libtmux.Session | None = field(default=None, init=False, repr=False)

    # Pool state — guarded by _lock
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _available: deque[PooledTmuxTerminal] = field(
        default_factory=deque, init=False, repr=False
    )
    _all_panes: list[PooledTmuxTerminal] = field(
        default_factory=list, init=False, repr=False
    )
    _semaphore: threading.Semaphore = field(init=False, repr=False)

    _initialized: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _initial_window: libtmux.Window | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_panes < 1:
            raise ValueError(f"max_panes must be >= 1, but got {self.max_panes}.")
        self._semaphore = threading.Semaphore(self.max_panes)

    def initialize(self) -> None:
        """Create the tmux session (panes are lazily added on checkout)."""
        if self._initialized:
            return

        env = sanitized_env()
        self._server = libtmux.Server(socket_name=TMUX_SOCKET_NAME, environment=env)
        session_name = f"openhands-pool-{self.username}-{uuid.uuid4()}"
        self._session = self._server.new_session(
            session_name=session_name,
            start_directory=self.work_dir,
            kill_session=True,
            x=TMUX_SESSION_WIDTH,
            y=TMUX_SESSION_HEIGHT,
        )
        for k, v in env.items():
            self._session.set_environment(k, v)
        self._session.set_option("history-limit", str(HISTORY_LIMIT))

        # Keep a reference to the default window so we can kill it once
        # the first real pane window is created (tmux requires at least
        # one window to keep the session alive).
        self._initial_window = self._session.active_window

        self._initialized = True
        logger.info(
            "TmuxPanePool initialized: "
            f"session={session_name}, max_panes={self.max_panes}"
        )

    def close(self) -> None:
        """Destroy all panes and the tmux session."""
        if self._closed:
            return
        self._closed = True

        with self._lock:
            for terminal in self._all_panes:
                terminal._closed = True
            self._all_panes.clear()
            self._available.clear()

        # Kill the entire tmux session (destroys all windows/panes at once).
        # We deliberately skip per-terminal close() because that also calls
        # session.kill() and would fail on the second pane.
        try:
            if self._session is not None:
                self._session.kill()
        except Exception as e:
            logger.warning(f"Error killing pool session: {e}")

    def _create_pane(self) -> PooledTmuxTerminal:
        """Create a new PooledTmuxTerminal within the shared session."""
        assert self._session is not None

        shell_command = "/bin/bash"
        if self.username in ["root", "openhands"]:
            shell_command = f"su {self.username} -"

        window = self._session.new_window(
            window_name=f"pane-{len(self._all_panes)}",
            window_shell=shell_command,
            start_directory=self.work_dir,
        )
        active_pane = window.active_pane
        assert active_pane is not None

        # Kill the default window now that a real window exists.
        if self._initial_window is not None:
            with suppress(Exception):
                self._initial_window.kill()
            self._initial_window = None

        # Use PooledTmuxTerminal which overrides close() to only kill
        # this terminal's window instead of the entire shared tmux session.
        terminal = PooledTmuxTerminal(work_dir=self.work_dir, username=self.username)
        terminal.server = self._server  # type: ignore[assignment]
        terminal.session = self._session
        terminal.window = window
        terminal.pane = active_pane

        # Configure PS1 (same as TmuxTerminal.initialize)
        ps1 = terminal.PS1
        active_pane.send_keys(
            f'set +H; export PROMPT_COMMAND=\'export PS1="{ps1}"\'; export PS2=""'
        )
        time.sleep(0.1)
        terminal._initialized = True
        terminal.clear_screen()

        logger.debug(
            f"Created pooled pane #{len(self._all_panes)}: {active_pane.pane_id}"
        )
        return terminal

    def checkout(self, timeout: float | None = None) -> PooledTmuxTerminal:
        """Check out a pane from the pool, blocking if all are busy.

        Args:
            timeout: Max seconds to wait. None means wait forever.

        Returns:
            A PooledTmuxTerminal ready for use.

        Raises:
            RuntimeError: If the pool is closed or not initialized.
            TimeoutError: If *timeout* expires before a pane is available.
        """
        if not self._initialized or self._closed:
            raise RuntimeError("TmuxPanePool is not initialized or already closed")

        if timeout is None:
            self._semaphore.acquire()
        elif not self._semaphore.acquire(timeout=timeout):
            raise TimeoutError(
                f"No pane available within {timeout}s (pool size {self.max_panes})"
            )

        with self._lock:
            if self._available:
                terminal = self._available.popleft()
                logger.debug(f"Checked out existing pane: {_safe_pane_id(terminal)}")
                return terminal

            # Create a new pane (still under max_panes thanks to semaphore)
            terminal = self._create_pane()
            self._all_panes.append(terminal)
            logger.debug(f"Checked out new pane: {_safe_pane_id(terminal)}")
            return terminal

    def checkin(self, terminal: PooledTmuxTerminal) -> None:
        """Return a pane to the pool."""
        with self._lock:
            if terminal not in self._all_panes:
                logger.warning("Attempted to checkin a pane not from this pool")
                return
            if not self._closed:
                self._available.append(terminal)

        self._semaphore.release()
        logger.debug(f"Checked in pane: {_safe_pane_id(terminal)}")

    def replace(self, old_terminal: PooledTmuxTerminal) -> PooledTmuxTerminal:
        """Replace a checked-out pane with a fresh one.

        The caller must currently hold *old_terminal* (i.e. it was
        checked out and not yet checked in).  The old terminal is
        closed and removed from the pool, and a brand-new pane is
        returned **in its place** — the semaphore count is unchanged
        because we swap 1-for-1.
        """
        with self._lock:
            # Create the replacement pane BEFORE killing the old window,
            # because tmux destroys the session when the last window dies.
            new_terminal = self._create_pane()
            self._all_panes.append(new_terminal)

            if old_terminal in self._all_panes:
                self._all_panes.remove(old_terminal)
            if old_terminal in self._available:
                self._available.remove(old_terminal)

        # Capture IDs before killing (repr would fail after kill).
        old_pane_id = _safe_pane_id(old_terminal)
        new_pane_id = _safe_pane_id(new_terminal)

        # Only destroy the old terminal's window — NOT terminal.close()
        # which would kill the entire shared tmux session.
        try:
            old_terminal.window.kill()
        except Exception as e:
            logger.debug(f"Error killing replaced pane window: {e}")
        old_terminal._closed = True

        logger.debug(f"Replaced pane {old_pane_id} -> {new_pane_id}")
        return new_terminal

    @contextmanager
    def pane(self, timeout: float | None = None) -> Iterator[PaneHandle]:
        """Context manager: checkout a pane, yield a handle, checkin on exit.

        The yielded :class:`PaneHandle` is mutable — callers that call
        :meth:`replace` should assign the new terminal back to
        ``handle.terminal`` so that the correct pane is checked in.
        """
        handle = PaneHandle(self.checkout(timeout=timeout))
        try:
            yield handle
        finally:
            self.checkin(handle.terminal)
