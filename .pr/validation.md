# Validation for model-directed context resets

Source revision: `20532dd3e5fb88e75e880ce07a6afbdff98df1b3`.
Base main: `8ba966d1d`. macOS arm64, Python 3.13.15, SDK/Agent Server 1.53.0 development tree.
The source tree is identical to the tested pre-rebase revision `781da62aff238d3f9609c88337198c853e0ac658`; only commit ancestry changed.

## Local checks

| Check | Result |
| --- | --- |
| `make build` | Passed in both isolated SDK worktrees |
| Changed-file and source-commit pre-commit hooks | Passed, including pyright, lint, import and tool-registration checks |
| `uv run pytest tests/sdk -q -n 4` | 6,982 passed; 9 skipped; 11 xfailed; 1 non-strict xpassed (7,003 collected) |
| New Agent reset lifecycle/capacity tests | 30 passed, sync and async |
| History retrieval tests on history revision `7ba20ec00` | 18 passed |
| `uv run pytest tests/agent_server tests/cross -q -n 4` | 2,992 passed; 9 failed; 1 skipped (3,002 collected) |
| New settings → create → real local Agent Server REST/WebSocket reset test | Passed within the cross-runtime suite |
| Persisted settings compatibility script | 23 golden fixtures and 8 PyPI 1.53.0 baseline payloads passed |
| Public API compatibility script | No breaking changes against PyPI 1.53.0 in SDK, workspace or tools; ACP dependency check skipped without a base-ref environment variable |
| OpenAPI tests / quality checker | 8 passed; existing 62 allowlisted cases unchanged |
| TypeScript coverage suite | 384 passed; final additive type refinement additionally passed focused tests and strict compilation |
| TypeScript build, lint, format, public type budget | Passed (lint retains 7 existing warnings) |
| Both offline examples | Passed; reset example executes ordinary file writes/reads and history retrieval, with zero-cost scripted model responses |
| Real Canvas UI with scripted HTTP provider | 1 passed, 11.828 seconds; five successful tool results, one reset, zero summaries; [recording and assertions](evidence/canvas/README.md) |
| Real-model behavior test `b06_agent_reset_history` | Collector/import and hooks passed; real inference not run without configured model credentials |

## Broad server-suite failures reproduced on untouched main

An independent worktree at `8ba966d1d`, with its own `make build`, reproduced all nine failing node IDs (9 failed, 2 passed in the targeted baseline run):

- Seven tests in `tests/agent_server/canvas_extensions/test_canvas_extension_backend.py`: Linux backend fixtures are unsupported on this macOS host.
- `test_cleanup_stale_tmux_sessions_includes_isolated_sockets`: the tmux executable is absent.
- `test_server_info_reports_configured_conversation_runtime`: the Docker executable is absent.

These results are not a completely green server CI matrix. They establish that the same failures occur without the feature. Unrelated production code and tests were not changed to hide them.

## Behavior and evidence boundaries

The tests assert actual model inputs, tool outcomes, event persistence/reopen/fork, and REST/WebSocket round trips. They cover asynchronous input arriving during generation, full tool batches, Finish precedence, approval rejection, interrupt after a committed tool result, request/condensation persistence gaps, unknown model capacity, protected handoff overflow, and a bounded outer summary rescue.

The scripted examples and Canvas demonstration establish harness behavior. They do not establish how reliably a real model decides when to reset, what to preserve in handoff, or when to search history. The live behavior test reports voluntary resets separately from summary rescues and accepts either sufficient search snippets or explicit reads.

Remote CI, model-backed behavioral evaluation, and maintainer acceptance of the adjusted issue design remain separate requirements. This work does not claim automatic merge readiness.

