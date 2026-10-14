# Review: exception-safe tmux pane checkout and explicit recovery

Status: **reviewed by the human contributor; submitted as a draft pending the contributor's required HUMAN description field.**

- Upstream clone: `https://github.com/OpenHands/software-agent-sdk.git`
- Branch: `fix/tmux-pool-checkout-recovery`
- Base: `608a102c637d8d8a999f49d7b04846524bd8bd1c`
- Runtime incident: SDK/tools 1.51.0, libtmux 0.62.0, Linux amd64 task image on Rosetta. This checkout is upstream main (SDK 1.53.0); **the whole checkout must not replace the frozen 1.51.0 experiment**.

## What is fixed

1. `TmuxPanePool.checkout()` releases its acquired semaphore permit if checkout fails, including cancellation. Existing panes are not removed from the available deque before potentially failing log preparation.
2. Failed pane initialization closes the newly allocated window and preserves the initial window until initialization succeeds. This allows retry without leaking windows or destroying the shared session.
3. Pool lifecycle logs use the stored pane ID, never the live libtmux `Pane.__repr__`, including when DEBUG is enabled.
4. `TmuxObjectDoesNotExist` is classified by type, not a partial match on its generic `Could not find object` message.
5. `read_screen()` explicitly raises on a failed `capture-pane`; recovery recognizes tmux's `can't find pane/window` errors. This is necessary because removing side-effectful logging otherwise removes the accidental failure detection used by shell-exit recovery.

Failed commands are not automatically replayed. Public schemas, tool options, default deadlines, model configuration, and agent policy are unchanged.

## Scope boundaries

- This does **not** claim to reproduce or solve the final historical expr grep stall. Its final thread stack was not preserved.
- Bounded pool acquisition, tmux subprocess deadlines, and cancellation of blocked subprocess creation are **not** implemented in this change. A complete deadline/cleanup design should be a separate PR, not a thread-future timeout that leaves work running.

## Evidence

### Red → green

On the unmodified base:

- Pool logging after a real tmux server loss fails at both INFO and DEBUG levels.
- Repeated failed pane setup leaks state/capacity.
- Injecting the exact observed `TmuxObjectDoesNotExist('Could not find object')` through `TerminalExecutor` escapes instead of producing a recovery observation.

These regression tests pass with the patch. The exact exception text injection models the historical stack; it is not claimed to recreate the original timing race.

A real terminal session deletion while a sibling keeps the tmux server alive is also tested. This scenario passes on the original base and final patch; it specifically caught an intermediate regression when logging stopped detecting failures. It is compatibility coverage, not evidence that the original incident was reproduced end to end.

### Tests run

- Full terminal suite, macOS + real tmux + workspace locked dependencies:
  **357 passed, 15 skipped**, 346.66 seconds. The skipped cases are platform-dependent, not claimed as Windows validation.
- Focused pane-pool and executor integration suites: **40 passed**.
- Same focused suites in an isolated environment explicitly pinned to **libtmux 0.62.0**: **40 passed**.
- Three source-file patch applies cleanly to upstream **v1.51.0** (`a955aa5d3188d4b0a44ad7eb4e5c4bba6e6238d9`).
- The three-file backport was tested only in a disposable network-disabled Linux container using the original frozen SDK/tools **1.51.0**, libtmux **0.62.0**, and original tmux wrapper. Repeated setup failure, logging after server loss, exact missing-object recovery, and real shell-exit recovery all passed. No model requests.

The last check is reproducible with `.pr/verify_terminal_backport.py`; this is a development-only smoke script, not a deployment change. Logs are in `.pr/evidence/`.

### Commands

The development environment is ready. To use it without re-resolving dependencies:

```sh
TMPDIR=/tmp .venv/bin/python -m pytest tests/tools/terminal --forked --timeout=50 -q
```

Use a current uv supporting the repository's `exclude-newer = "7 days"` setting, with `UV_FROZEN=1`, for pre-commit. `TMPDIR=/tmp` avoids macOS's long default temporary path exceeding tmux's UNIX socket path limit. No dependency or lockfile changes are included.

## Review entry points

```sh
git diff --stat
git diff -- openhands-tools/openhands/tools/terminal/
git diff -- tests/tools/terminal/test_tmux_pane_pool.py tests/tools/terminal/test_pool_integration.py
```

No dependency, package-version, public schema, or default deadline changes are included. The historical final hang remains unattributed; this PR is deliberately scoped to the reproducible resource-management and error-recovery defects above.
