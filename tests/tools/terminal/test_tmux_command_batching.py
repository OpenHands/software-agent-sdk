import os
import shlex
import time
import uuid

import libtmux
import libtmux.common
import pytest

from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool
from openhands.tools.terminal.terminal.tmux_terminal import run_tmux_commands


pytestmark = pytest.mark.skipif(os.name != "posix", reason="tmux requires Unix sockets")


def _initialize_counting_tmux_calls(tmp_path, monkeypatch) -> tuple[TmuxPanePool, int]:
    calls = 0
    real_init = libtmux.common.tmux_cmd.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(libtmux.common.tmux_cmd, "__init__", counting_init)
    pool = TmuxPanePool(work_dir=str(tmp_path), username=None, max_panes=1)
    pool.initialize()
    monkeypatch.setattr(libtmux.common.tmux_cmd, "__init__", real_init)
    return pool, calls


def test_pool_startup_tmux_calls_do_not_grow_with_environment(tmp_path, monkeypatch):
    baseline_pool, baseline = _initialize_counting_tmux_calls(tmp_path, monkeypatch)
    baseline_pool.close()
    for i in range(300):
        # Credential slots are sanitized away (unset in tmux); the others are set.
        prefix = "OH_SESSION_API_KEYS_" if i % 2 else ""
        monkeypatch.setenv(f"{prefix}TEST_BATCH_VAR_{i}", f"value {i} " + "x" * 40)

    pool, calls = _initialize_counting_tmux_calls(tmp_path, monkeypatch)
    pool.close()

    # Batches are size-bounded, so a large environment adds a few calls, not one
    # per variable.
    assert calls - baseline <= 6


def test_pool_starts_with_undecodable_environment_value(tmp_path, monkeypatch):
    # Python keeps environment bytes that are not UTF-8 as surrogates.
    monkeypatch.setenv("TEST_UNDECODABLE", os.fsdecode(b"caf\xe9"))
    pool = TmuxPanePool(work_dir=str(tmp_path), username=None, max_panes=1)
    try:
        pool.initialize()
    finally:
        pool.close()


def test_pool_shells_see_tricky_values(tmp_path):
    values = {
        "A_SPACES": "two words",
        "B_INNER_SEMICOLON": "a;b",
        "C_QUOTES": 'it\'s "quoted"',
        "D_DOLLAR": "$HOME and ${PATH}",
        "E_TRAILING_SEMICOLON": "history -a;",
        "F_AFTER_TRAILING": "still set",
    }
    pool = TmuxPanePool(work_dir=str(tmp_path), username=None, max_panes=1, env=values)
    pool.initialize()
    assert pool._session is not None
    dump = tmp_path / "env.txt"
    partial = shlex.quote(str(dump) + ".partial")
    try:
        # The window must outlive new_window(), which looks it up afterwards.
        pool._session.new_window(
            window_shell=(
                f"env > {partial} && mv {partial} {shlex.quote(str(dump))}; sleep 60"
            )
        )
        deadline = time.monotonic() + 10
        while not dump.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        seen = dict(
            line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line
        )
    finally:
        pool.close()

    for name, value in values.items():
        if name != "E_TRAILING_SEMICOLON":
            assert seen[name] == value
    assert "E_TRAILING_SEMICOLON" in seen


def test_batches_are_sized_in_utf8_bytes():
    server = libtmux.Server(socket_name=f"batch-test-{uuid.uuid4().hex[:8]}")
    session = server.new_session(window_command="sleep 60")
    # 2,500 characters but 7,500 bytes each: together under 8,000 characters
    # but over tmux's ~16 KiB message limit.
    values = {f"WIDE_{i}": "€" * 2500 for i in range(3)}
    target = str(session.session_id)
    try:
        run_tmux_commands(
            server,
            [["set-environment", "-t", target, k, v] for k, v in values.items()],
        )
        environment = session.show_environment()
    finally:
        server.kill()

    assert {name: environment[name] for name in values} == values
