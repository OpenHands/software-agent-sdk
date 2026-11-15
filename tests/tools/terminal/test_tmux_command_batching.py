import os
import shlex
import time

import libtmux.common
import pytest

from openhands.tools.terminal.terminal.tmux_pane_pool import TmuxPanePool


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


@pytest.mark.parametrize("extra_vars", [0, 300])
def test_pool_startup_tmux_calls_do_not_grow_with_environment(
    tmp_path, monkeypatch, extra_vars
):
    baseline_pool, baseline = _initialize_counting_tmux_calls(tmp_path, monkeypatch)
    baseline_pool.close()
    for i in range(extra_vars):
        # OH_* names are sanitized away (unset in tmux); the others are set.
        prefix = "OH_" if i % 2 else ""
        monkeypatch.setenv(f"{prefix}TEST_BATCH_VAR_{i}", f"value {i} " + "x" * 40)

    pool, calls = _initialize_counting_tmux_calls(tmp_path, monkeypatch)
    pool.close()

    # Batches are size-bounded, so a large environment adds a few calls, not one
    # per variable.
    assert calls - baseline <= extra_vars // 50


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
        pool._session.new_window(
            window_shell=f"env > {partial} && mv {partial} {shlex.quote(str(dump))}"
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
