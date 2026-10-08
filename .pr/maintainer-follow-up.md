# Maintainer review follow-up — PR #5567

Addresses all eight comments from VascoSch92 on reviewed head
`943d102d0bb37bc77bc53fe37f8813f0096e2775`.

## Changes

| Thread | Implementation | Observable coverage |
| --- | --- | --- |
| F1: one pane kills siblings | Replace a failed pane while its checkout is still owned, before checkin. Only server/session errors go directly to pool rebuild; a failed local replacement can escalate to that path. Generic missing-object errors try local replacement first. No interrupted command is replayed. | Real shell exit while another pane waits on a file barrier; generic missing-object fault with the same running sibling. |
| F2: old-pool waiter hangs | Track outstanding checkout identities independently of the live pane list. Close wakes a waiter, and each closed checkout releases the wakeup permit for the next. Valid late checkins release once; duplicate/foreign returns do not increase capacity. | Close wakes more waiters than pool capacity before borrowers return; delayed old borrower through real server-loss recovery; duplicate checkin cannot overbook. |
| F3: raw closed-pool RuntimeError | A RuntimeError subclass identifies pool shutdown. Executor waits for concurrent recovery to finish publishing the replacement and returns the recovery observation if this was a retired pool. Unrelated RuntimeErrors and intentional pool shutdown are not swallowed. | Concurrent holder/borrower/waiter workflow and the unmodified F3 reproducer. |
| F4: non-pooled compatibility | Strict capture failure handling is enabled only by PooledTmuxTerminal via a private class flag. The standalone factory/backend keeps its original empty-screen/timeout observation behavior. | Public create_terminal_session(tmux) followed by execute(exit); standalone stale-socket read isolation. |
| F5: stderr message | Join stderr lines; use `capture-pane failed (rc=N)` when empty and recognize that fallback as recoverable. | Fault-injected failed capture through the real executor, readable observation, no replay, healthy next call. |
| F6: shell dies in setup | Strictly capture once after setup, before retiring the initial window. A vanished pane now takes the existing setup rollback path. | Kill the real setup window from clear_screen, verify checkout raises and all capacity is usable afterward. |
| F7: slow creation holds pool lock | A semaphore permit reserves creation capacity; create/replace tmux work runs outside the pool lock. Registration rechecks shutdown and disposes of unregistered panes on failure. Window names are unique under concurrent creation. | Pause real pane setup while another thread checks in/reborrows a warm pane or closes the pool, for both checkout and replace. |
| frozenset | Both server markers and combined recovery markers are immutable sets. | Static lint/type checks; all recovery workflows above. |

## Authentic before/after evidence

`.pr/run_review_reproducers.py` executes the seven Python snippets posted by the
maintainer, each in an isolated Python subprocess. It adds resource cleanup but
leaves each snippet unchanged. Commands ran with real tmux **3.6a**, macOS,
Python **3.13.7**, and workspace-locked libtmux **0.53.0**.

```sh
TMPDIR=/tmp uv run --frozen python .pr/run_review_reproducers.py
```

- [Before](evidence/review-before.log): F1–F7 all demonstrate the reported issue
  on reviewed head 943d102.
- [After](evidence/review-after.log): sibling completes; all waiters finish;
  concurrent old-pool checkout returns an observation; standalone exit returns
  `exit_code=-1`; stderr is readable and empty stderr recoverable; dead setup
  is rejected; warm checkin drops from 256 ms to 1 ms.
- The F6 snippet deliberately uses `SystemExit("not reproduced: setup failure
  was detected")` on success, so its after-run process status is **1**, not a
  failing fix. Its output and the committed regression test establish the result.
- F2 deliberately schedules the narrow interleaving with delays; F5's empty
  stderr is injected; F6 kills a real shell during setup. These limitations are
  unchanged from the maintainer's reproducers.

## Regression checks

```sh
make build
TMPDIR=/tmp uv run --frozen pytest tests/tools/terminal/test_tmux_pane_pool.py \
  tests/tools/terminal/test_pool_integration.py --forked --timeout=50 -q
TMPDIR=/tmp uv run --frozen --with libtmux==0.62.0 pytest \
  tests/tools/terminal/test_tmux_pane_pool.py \
  tests/tools/terminal/test_pool_integration.py --forked --timeout=50 -q
TMPDIR=/tmp uv run --frozen pytest tests/tools/terminal -n 4 --forked --timeout=50 -q
```

- [Focused suite](evidence/review-focused.log): **54 passed**.
- [libtmux 0.62.0 overlay](evidence/review-libtmux-062.log): **54 passed**, without
  modifying workspace dependencies or the lockfile.
- [Full terminal suite](evidence/review-terminal-suite.log): **371 passed, 15 skipped**
  on final code commit `4620a03`. The parallel/forked run also emits Python's
  multithreaded-fork deprecation warnings from pytest-forked.
- Pre-commit (including Pyright, Ruff, import rules and registration checks) passes.

`make build` initially found uv 0.8.22, which cannot parse the repository's
seven-day cutoff. Its generated lockfile change was discarded, uv was upgraded
to 0.12.23, and `make build` restored the locked environment. No dependency,
lockfile or package-version changes are included.

## Boundaries

No Windows validation, new Linux runtime evidence, or model-driven benchmark is
claimed here; CI must validate the pushed head. The older Linux/backport logs in
this PR describe the earlier patch, not this review follow-up. No tmux subprocess
hard deadline or interrupted-command replay has been added. The setup check
catches already-vanished panes; it cannot prevent a shell exiting immediately
after a successful check. Existing pool initialization/shutdown ownership beyond
these checked-out/in-flight panes is not redesigned.

The HTML design page and these artifacts are temporary reviewer context. Preserve
commit-pinned links if `.pr/` is removed before merge.
