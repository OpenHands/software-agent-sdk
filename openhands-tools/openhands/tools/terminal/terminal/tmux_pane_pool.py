"""Pool of tmux panes for parallel terminal command execution.

Maintains a fixed-size pool of TmuxTerminal instances within a single
tmux session, enabling concurrent command execution across panes.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager

import libtmux

from openhands.sdk.logger import get_logger
from openhands.sdk.utils import sanitized_env
from openhands.tools.terminal.constants import HISTORY_LIMIT
from openhands.tools.terminal.metadata import CmdOutputMetadata
from openhands.tools.terminal.terminal.tmux_terminal import TmuxTerminal


logger = get_logger(__name__)

DEFAULT_MAX_PANES = 4


class TmuxPanePool:
    """Thread-safe pool of tmux panes for parallel terminal execution.

    Each pane is a fully configured TmuxTerminal sharing a single tmux
    session.  Callers check out a pane, run commands, and check it back
    in.  A semaphore limits concurrency to ``max_panes``.

    Usage::

        pool = TmuxPanePool("/workspace", max_panes=4)
        pool.initialize()

        with pool.pane() as terminal:
            terminal.send_keys("echo hello")
            output = terminal.read_screen()

        pool.close()
    """

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        max_panes: int = DEFAULT_MAX_PANES,
    ) -> None:
        if max_panes < 1:
            raise ValueError("max_panes must be >= 1")

        self.work_dir = work_dir
        self.username = username
        self.max_panes = max_panes

        self._server: libtmux.Server | None = None
        self._session: libtmux.Session | None = None

        # Pool state — guarded by _lock
        self._lock = threading.Lock()
        self._available: deque[TmuxTerminal] = deque()
        self._all_panes: list[TmuxTerminal] = []
        self._semaphore = threading.Semaphore(max_panes)

        self._initialized = False
        self._closed = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Create the tmux session (panes are lazily added on checkout)."""
        if self._initialized:
            return

        env = sanitized_env()
        self._server = libtmux.Server(socket_name="openhands", environment=env)
        session_name = f"openhands-pool-{self.username}-{uuid.uuid4()}"
        self._session = self._server.new_session(
            session_name=session_name,
            start_directory=self.work_dir,
            kill_session=True,
            x=1000,
            y=1000,
        )
        for k, v in env.items():
            self._session.set_environment(k, v)
        self._session.set_option("history-limit", str(HISTORY_LIMIT))

        # Kill the default window — panes will each get their own window
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
                try:
                    terminal.close()
                except Exception as e:
                    logger.debug(f"Error closing pooled pane: {e}")
            self._all_panes.clear()
            self._available.clear()

        try:
            if self._session is not None:
                self._session.kill()
        except Exception as e:
            logger.debug(f"Error killing pool session: {e}")

    # ------------------------------------------------------------------
    # Checkout / Checkin
    # ------------------------------------------------------------------

    def _create_pane(self) -> TmuxTerminal:
        """Create a new TmuxTerminal within the shared session."""
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

        # Build a TmuxTerminal that reuses this session's window/pane
        terminal = TmuxTerminal.__new__(TmuxTerminal)
        terminal.work_dir = self.work_dir
        terminal.username = self.username
        terminal._initialized = False
        terminal._closed = False
        terminal.PS1 = CmdOutputMetadata.to_ps1_prompt()
        terminal.server = self._server  # type: ignore[assignment]
        terminal.session = self._session
        terminal.window = window
        terminal.pane = active_pane

        import time

        # Configure PS1 (same as TmuxTerminal.initialize)
        ps1 = terminal.PS1
        active_pane.send_keys(
            f'set +H; export PROMPT_COMMAND=\'export PS1="{ps1}"\'; export PS2=""'
        )
        time.sleep(0.1)
        terminal._initialized = True
        terminal.clear_screen()

        logger.debug(f"Created pooled pane #{len(self._all_panes)}: {active_pane}")
        return terminal

    def checkout(self, timeout: float | None = None) -> TmuxTerminal:
        """Check out a pane from the pool, blocking if all are busy.

        Args:
            timeout: Max seconds to wait. None means wait forever.

        Returns:
            A TmuxTerminal ready for use.

        Raises:
            RuntimeError: If the pool is closed or not initialized.
            TimeoutError: If *timeout* expires before a pane is available.
        """
        if not self._initialized or self._closed:
            raise RuntimeError("TmuxPanePool is not initialized or already closed")

        if not self._semaphore.acquire(timeout=timeout if timeout is not None else -1):
            raise TimeoutError(
                f"No pane available within {timeout}s (pool size {self.max_panes})"
            )

        with self._lock:
            if self._available:
                terminal = self._available.popleft()
                logger.debug(f"Checked out existing pane: {terminal.pane}")
                return terminal

            # Create a new pane (still under max_panes thanks to semaphore)
            terminal = self._create_pane()
            self._all_panes.append(terminal)
            logger.debug(f"Checked out new pane: {terminal.pane}")
            return terminal

    def checkin(self, terminal: TmuxTerminal) -> None:
        """Return a pane to the pool."""
        with self._lock:
            if terminal not in self._all_panes:
                logger.warning("Attempted to checkin a pane not from this pool")
                return
            if not self._closed:
                self._available.append(terminal)

        self._semaphore.release()
        logger.debug(f"Checked in pane: {terminal.pane}")

    @contextmanager
    def pane(self, timeout: float | None = None) -> Generator[TmuxTerminal]:
        """Context manager for checkout/checkin.

        Usage::

            with pool.pane() as terminal:
                terminal.send_keys("ls")
                print(terminal.read_screen())
        """
        terminal = self.checkout(timeout=timeout)
        try:
            yield terminal
        finally:
            self.checkin(terminal)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Number of panes currently created (may be < max_panes)."""
        with self._lock:
            return len(self._all_panes)

    @property
    def available_count(self) -> int:
        """Number of panes currently idle in the pool."""
        with self._lock:
            return len(self._available)
