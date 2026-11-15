# Lean models of the conversation-start fixes

Small Lean 4 models (core Lean only, no Mathlib) of the changes in the
conversation-start stack (#6870 → #6881) that change behavior rather than only
speed. Each model states the property the change must keep and proves it for
every input or interleaving. Where a protection matters, the model also has
the variant without it and a computed counterexample, which shows the model
can see the bug.

```
cd formal/conversation-start
lake build                     # Lean v4.35.0, about 10 s
lake env lean AxiomAudit.lean  # every theorem: no sorryAx
```

| File | Code it models | Main results |
|---|---|---|
| `TmuxBatch.lean` | `run_tmux_commands` (#6871) and how tmux splits one invocation into commands | `exec_equiv`: the batched invocations run exactly the commands one call per command ran, in order. `plan_fits`: every invocation fits the size limit unless it is one command that alone exceeds it. `plan_length_le`: never more invocations than commands |
| `FetchFreshness.lean` | `_get_worktree_start_point`, `_fetched_recently` (#6873) | `fetchHead_stale`: trusting `FETCH_HEAD`'s mtime skips the origin fetch after a fetch of another remote (counterexample). `recorded_fresh`: with the fix, a skipped fetch always has a real origin fetch < 300 s behind it |
| `LateMcp.lean` | `_add_late_mcp_tools`, `close` (#6877) | `no_leak`: after `close()` every late MCP server is closed or never started. `no_add_after_close`. `unlocked_leaks`: without `_late_mcp_lock` a server's tools survive `close()` (counterexample) |
| `SkillsSingleFlight.lean` | `load_public_skills` (#6874) | `locked_loads_le`: at most one load per cache lifetime. `unlocked_double_load`: without the lock two callers both load (counterexample) |

## Bugs found and fixed

* **#6871, tmux batching.** `plan_fits` holds only if `len` measures what
  tmux limits, which is bytes. The code counted characters, so three
  environment values of 2,500 `€` (7,587 characters, 22,587 bytes) failed with
  `command too long` where one call per variable worked. Fixed in b9e55b6ca;
  `tests/tools/terminal/test_tmux_command_batching.py::test_batches_are_sized_in_utf8_bytes`
  fails without the fix.
* **#6873, fetch freshness.** `fetchHead_stale` is a real trace: after
  `git fetch upstream`, new worktrees started from an old `origin/main` for
  five minutes. Fixed in c43a884c3 (remember the server's own successful
  `git fetch origin`);
  `test_get_worktree_start_point_fetches_origin_after_another_remote` fails
  without the fix.

## Checked without a model

* **#6872** runs worktree git steps one at a time under an `asyncio.Lock`,
  the same serialization the event loop gave before.
* **#6880** lets two creates on one workspace run `git init` at once. In 200
  trials with 8 concurrent threads, the directory was always a valid repo
  afterwards. The losers only log `could not lock config file`.
* **#6879** changes only the lease lock's poll interval.
* **#6881** drops skills from the WebSocket state snapshot. REST responses
  already dropped them on main (`trim_conversation_response_skills`), and the
  SDK client reads `activated_knowledge_skills` and `invoked_skills`, which
  are kept.

## What the models leave out

* `TmuxBatch` does not model tmux errors. A failing command stops tmux at that
  command, and the code raises on any stderr, as one call per command did.
* `LateMcp` models one conversation. It does not cover a server that finishes
  with all its tools filtered out by `filter_tools_regex`, or with no tools:
  nothing then refers to its client, so `close()` cannot close it. One
  connection per server predates the stack, so this already happens on main
  for servers that make the budget.
* `SkillsSingleFlight` models one cache key. The lock is global, so a caller
  with a fresh cache can wait behind a slow load of another skills repository.
  That costs latency, not correctness.
* `FetchFreshness` keeps the 300 s window #6873 chose: a push to origin after
  the server's own fetch is picked up by the next fetch, up to 300 s later.
