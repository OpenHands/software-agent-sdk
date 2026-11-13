"""Tests for the tmux wait-for out-of-band completion signal.

A very long single-line command can garble the echoed sentinel in the pane
(fast large echo interleaving with command output), leaving the pane-text
regex unable to match and the wait loop spinning forever. The tmux backend
appends a ``tmux wait-for -S`` signal to the boundary suffix and blocks on
the same channel from the driver side, so completion no longer depends on
the sentinel surviving in the pane text.
"""

import tempfile

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session


def _tmux_session(tmp_dir: str):
    session = create_terminal_session(work_dir=tmp_dir, terminal_type="tmux")
    session.initialize()
    return session


def test_wait_for_completes_when_sentinel_scrolls_out_of_history(tmp_path):
    """Flood the pane past the history limit so the sentinel is unreadable.

    Only the wait-for signal can end the wait here; without it the session
    hangs until the hard timeout.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _tmux_session(tmp_dir)
        try:
            # 3000 lines > history limit (2000): the sentinel echo printed
            # after the flood cannot be captured from the pane anymore.
            flood = "; ".join([f"echo line-{i:05d}" for i in range(3000)])
            action = TerminalAction(command=f"({flood}) > /dev/null", timeout=60.0)
            obs = session.execute(action)
            assert "completed with exit code 0" in obs.metadata.suffix, obs.text
        finally:
            session.close()


def test_wait_for_normal_completion(tmp_path):
    """An ordinary heredoc-boundary command completes via either path."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _tmux_session(tmp_dir)
        try:
            action = TerminalAction(command="python -c 'print(1 + 1)'", timeout=30.0)
            obs = session.execute(action)
            assert "2" in obs.text
            assert "completed with exit code 0" in obs.metadata.suffix
        finally:
            session.close()


def test_concurrent_sessions_unique_channels(tmp_path):
    """Two sessions signal distinct channels; no cross-wake."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        s1 = _tmux_session(tmp_dir)
        s2 = _tmux_session(tmp_dir)
        try:
            obs1 = s1.execute(TerminalAction(command="echo one", timeout=30.0))
            obs2 = s2.execute(TerminalAction(command="echo two", timeout=30.0))
            assert "one" in obs1.text
            assert "two" in obs2.text
        finally:
            s1.close()
            s2.close()


def test_signal_snippet_references_server_socket(tmp_path):
    """The snippet must hardcode the session's own server socket."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _tmux_session(tmp_dir)
        try:
            snippet = session.terminal.completion_signal_snippet("some_channel")
            assert "wait-for -S some_channel" in snippet
            # -L named socket or -S absolute path, but always explicit.
            assert "-L" in snippet or "-S" in snippet
        finally:
            session.close()


def test_waiter_release_leaves_no_blocked_clients(tmp_path):
    """After completion the waiter client is released, not leaked."""
    import subprocess

    with tempfile.TemporaryDirectory() as tmp_dir:
        session = _tmux_session(tmp_dir)
        try:
            session.execute(TerminalAction(command="echo done", timeout=30.0))
            waiter_procs = subprocess.run(
                ["pgrep", "-fl", "wait-for __openhands_cmd_"],
                capture_output=True,
                text=True,
            )
            assert "wait-for __openhands_cmd_" not in waiter_procs.stdout
        finally:
            session.close()
