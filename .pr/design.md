# Design: `POST /api/conversations/{id}/execute_tool`

Closes #6094. Revives the server-side dependency of the two stale PRs the issue
cites (OpenHands/OpenHands#13285 + SDK#2356).

## Problem

A workspace bootstrap script such as `.openhands/setup.sh` exists to mutate the
environment (`export PATH=...`, `nvm use`, `pyenv activate`, sourced files).
Neither mechanism available today can make those changes visible to the agent's
later `terminal` commands:

| Mechanism | How it runs | Env survives? |
|---|---|---|
| `.openhands/setup.sh` (app-server only) | `AsyncRemoteWorkspace.execute_command()` -> `POST /api/bash/execute_bash_command` -> fresh `asyncio.create_subprocess_shell` | no |
| `session_start` hook | `HookExecutor` -> fresh `subprocess.run(..., shell=True)` | no |
| **`execute_tool` through the conversation's own terminal** | the conversation's `TerminalTool` tmux session | **yes** |

The in-process primitive already exists: `LocalConversation.execute_tool()`
(added in #1833). What was missing is the HTTP path to it plus the client
method, so any client that owns the convention (app-server, Agent Canvas) can
run the script on the persistent terminal.

## Shape

```
router  POST /api/conversations/{conversation_id}/execute_tool
          |  ExecuteToolRequest{tool_name, action} -> ExecuteToolResponse{observation}
          v
ConversationService.execute_tool  -> None when the id is unknown (router -> 404)
          v
EventService.execute_tool  -> loop.run_in_executor(None, ...) so the event loop
          |                   is not held while the tool runs
          v
LocalConversation.execute_tool(tool_name, action)   # the conversation's own
                                                     # tool executor = tmux session
```

Mirrors the existing `ask_agent` / `condense` / `navigate` verbs exactly:
Router -> ConversationService -> EventService -> LocalConversation.

Both typed clients expose it: the Python `RemoteConversation.execute_tool()` and
the TypeScript `RemoteConversation.executeTool()` /
`ConversationClient.executeTool()`, which unwraps `ExecuteToolResponse.observation`
back into the tool's own observation model. The TypeScript client gates the call
on the `direct_tool_execution_v1` capability that `/server_info` advertises,
rather than a package-version floor: the route ships in the same package version
as servers that predate it (this checkout's `ServerInfo.version` is `1.55.1`), so
a version compare would reject a working endpoint.

## Key decisions

- **The action is a plain JSON object, validated against the selected tool.**
  `ExecuteToolRequest.action` is `dict[str, Any]`; the server resolves
  `tool_name`, requires it to be one of `DIRECT_EXECUTION_TOOLS` (currently
  `terminal`), and validates the payload against **that tool's** `action_type`,
  so a payload for another tool is a 422 rather than a 500 out of the executor.
  See the OpenAPI note below for why the SDK `Action` union is not used.
- **Only the listed tools may run directly.** `LocalConversation.execute_tool()`
  is a generic primitive, but over HTTP a generic route would dispatch any tool,
  including `ClientTool` (which only reports that it was dispatched, because
  there is no `ActionEvent` to deliver it) and would skip the tool hooks and
  audit events the agent loop records. The allowlist keeps the endpoint to the
  pre-run setup case it exists for.
- **The direct call owns the conversation for its whole duration.** It runs
  under the per-conversation lifecycle and a direct in-flight flag, so a delete,
  an idle eviction, a run or another direct call cannot overlap it. Admission
  shares `_run_lock` with `run()`/`start_goal_loop()`, so a run cannot be
  admitted while the direct status read is pending and then start underneath the
  call. `run()`/`start_goal_loop()` while a direct call is in flight is 409
  (`api.py` registers a `ConversationBusyError` handler that maps it to 409, so
  every entry point that can admit a run — `/run`, `/goal`, and `/events` with
  `run=true` — answers 409 rather than letting a `RuntimeError` surface as a
  500), and a second direct call is 409. The call also draws on the server-wide
  run budget: it acquires the same `RunSlot` permit a run does before submitting
  to the executor, so direct work counts against `max_concurrent_runs` instead
  of queueing unboundedly in the process default executor. A full server answers
  429 with the same detail a run gets. The permit is held until the call
  settles, and on cancellation until the abandoned worker *and* its offloaded
  interrupt have finished, for the same reason the in-flight guard is. A call
  cancelled while still resolving the tool holds the permit too: the resolve
  future is shielded, so `_ensure_agent_ready` keeps initializing plugins and
  tools on its thread, and releasing the permit then would let another
  conversation run alongside that live initialization. The flag is released on success, on a
  validation failure, and, for a cancelled request whose synchronous worker
  already started, only when that worker actually exits — and, because the
  offloaded interrupt outlives the worker, only once that interrupt has also
  finished. A lingering interrupt can otherwise still scan for (and signal) the
  terminal process after the worker returns, killing the next admitted command,
  so the guard must cover the interrupt, not just the worker. `close()` returns
  the permit only after it has waited out the worker and drained the interrupts;
  if `close()` itself is cancelled mid-teardown, it aborts and leaves the
  release to the worker/interrupt done callbacks so the permit is not returned
  under still-live work.
- **A timed-out drain parks the rest of teardown.** The drain wait is bounded
  (`DIRECT_TOOL_DRAIN_TIMEOUT_SECONDS`), because a command that ignores the
  interrupt must not hold `close()` forever. On timeout the permit and the
  conversation are *not* released: teardown hands the remainder to a
  service-owned continuation that awaits the outstanding worker/interrupt
  futures and only then closes the conversation and returns the permit. So a
  worker that outlives the drain still counts against `max_concurrent_runs`
  and keeps its executor to itself, and teardown still completes once the work
  really stops. `close()` always observes that continuation's result, including
  when it has already completed, so it returns with the conversation fully torn
  down (what its callers — delete, eviction, shutdown — rely on) or raises the
  cleanup failure instead of reporting a false success. A retry `close()` joins
  a still-running continuation, while a *finished* one is dropped so a
  successful retry does not replay the stale failure.
- **Teardown survives cancellation of the caller.** `close()` hands the teardown
  to a task the service owns and awaits it through `asyncio.shield`, so
  cancelling the caller (a cancelled DELETE, say) cannot abort teardown
  partway. `_closing` latches and blocks idle eviction, so a half-finished
  teardown would leave the conversation, its executor and its lease allocated
  with nothing left to release them; only the teardown task finishes that work.
  A retry `close()` joins the same task, while a *finished* teardown is replaced
  so a failed `close()` stays retryable.
- **Cancellation returns promptly; teardown owns the worker and the
  interrupt.** Cancelling the request cannot stop the synchronous worker, and
  blocking the cancel on it would hold the request open for as long as the
  command ignores the interrupt. Instead the cancel offloads
  `executor.interrupt()` to a worker thread and returns; the service keeps the
  worker as `_direct_tool_worker` and the interrupt future in
  `_direct_tool_interrupt_tasks`, and `close()` (delete, idle eviction,
  shutdown) interrupts the running command and waits for its worker (bounded)
  before the conversation and its executors are torn down, so teardown cannot
  pull a terminal out from under a command mid-run. The interrupt is offloaded
  rather than called inline because the PowerShell backend's `interrupt()`
  performs two blocking child-process scans (each able to wait five seconds for
  a subprocess); calling it on the event loop would stall every request on that
  loop, `/alive` included. `close()` drains the interrupt futures too, so
  teardown cannot close an executor an offloaded interrupt is still using. The
  interrupt future carries a done callback that releases the in-flight guard
  once it (and the worker) have finished, so admission of the next direct call
  or run waits out the interrupt rather than racing it.
- **A direct call pins the terminal pane pool.** Shell state set by a bootstrap
  script (exports, functions, the working directory) is local to the one tmux
  pane that ran it, so the executor pins the pool to that pane; a later parallel
  command waits for it instead of silently landing on a fresh pane and losing the
  state.
- **The response carries the observation model, not text.** Tool-specific fields
  (`exit_code`, `command`, `content`) survive the round trip, so a caller can
  branch on the exit code of its bootstrap script. `is_error` is derivable from
  the observation, so no separate flag.
- **Unknown tool -> 422, unsupported tool -> 422, unknown conversation -> 404.**
  `KeyError` from the tool lookup maps to 422 with the tool named; a tool
  outside `DIRECT_EXECUTION_TOOLS`, or an action that is not valid for the named
  tool, is 422; `None` from the service maps to 404.
- **Non-blocking.** `EventService` runs the call in an executor, matching
  `ask_agent`; a slow bootstrap script does not stall `/alive`.
- **No tool event appended, confirmation/analyzer bypassed.** Inherited from
  `LocalConversation.execute_tool()` by design; it is a pre-run setup mechanism,
  documented in the route docstring and the OpenAPI description. The one
  exception is a conversation that has never run: the call initializes the agent
  first, which emits the initialization `SystemPromptEvent`, so the "no event"
  claim only holds once a message has already initialized the agent.
- **Non-goal: auto-discovery.** The agent-server loads no project convention on
  its own (`.openhands/hooks.json` is read only via `POST /api/hooks`), so
  auto-running `<workspace>/.openhands/setup.sh` belongs to the client that owns
  the convention, not this server. That wiring lives in `OpenHands/OpenHands`.

## OpenAPI note for reviewers

`action` is typed as a plain JSON object (`dict[str, Any]`), **not** the SDK
`Action` union. Two reasons:

- Correctness: the union accepts any registered action, so a `GrepAction` with
  `tool_name="terminal"` would validate and then reach `TerminalExecutor`, which
  cannot handle it. The server resolves `tool_name`, requires it to be in
  `DIRECT_EXECUTION_TOOLS` (currently `terminal`), and validates the payload
  against **that tool's** `action_type`: a mismatch is a 422, not a 500. The
  payload must also carry the `kind` discriminator naming that action; a body
  that omits it is 422 rather than being accepted as the named tool's action,
  which is what the request model documents and what the feature map asserts.
- Contract stability: typing the field as the union makes FastAPI split every
  action schema into `<Name>-Input` / `<Name>-Output` twins (38 renames,
  446 -> 486 schemas), renaming existing public components. With
  `dict[str, Any]` the public contract gains only `ExecuteToolRequest` /
  `ExecuteToolResponse` and the new path; no existing component is renamed (see
  the regenerated `clients/typescript/src/generated/agent-server-schema.ts`,
  which is generated from the pinned published spec and is unchanged).

One allowlist entry is added for the new
`ExecuteToolRequest/properties/action/additionalProperties` weak location
(63 -> 64 entries); no existing pointer changes. The REST breakage gate flags
`StartChildConversation*` oneOf additions **on the unmodified baseline too**,
i.e. pre-existing drift, not from this change.

## Evidence

- `control-agent-server map check` -> ok, 34 files, 963 ids.
- Live server (`control-agent-server exec` / `pytest tests/cross/...live_server.py
  -k execute_tool`) -> 7 passed: an `export` from one `execute_tool` is visible to
  the next in the same conversation (real `TerminalObservation`), a completed call
  is admitted again, a `GrepAction` with `tool_name="terminal"` is 422, an action
  without `kind` is 422, an unknown tool is 422 naming it, an unknown id is 404, an
  unauthenticated call is 401, a registered secret is masked, a dropped request
  does not stall `/alive`, and a direct call while another conversation holds the
  only server run permit is 429 but is admitted once the holder finishes. The
  run-limit case waits for the holder to actually start before probing, so it
  cannot flake by the probe winning the single permit first (it did, ~4/10
  before the fix; 10/10 after).
- Admission unit tests: `tests/agent_server/test_run_admission.py` holds two of
  four conversations' direct calls and a direct-vs-run competition on a shared
  blocking event; each fails if the `RunSlot.acquire` line is removed. The same
  file's `test_direct_call_keeps_its_permit_until_close_drains_it` closes a
  conversation mid-call and asserts a competing call is still 429 while
  `close()` drains the worker, then admitted once it returns; it fails if
  `close()` returns the permit before the drain. Two further regressions cover
  the cancellation windows: `test_cancelled_resolve_keeps_its_permit_until_initialization_finishes`
  cancels a call while `_resolve_direct_tool` is still blocked and asserts a
  competing call is 429 until initialization finishes, and
  `test_cancelled_close_keeps_its_permit_until_the_worker_exits` cancels
  `close()` while the worker is still running and asserts the permit is not
  returned under it. Both fail on the pre-fix head. A third,
  `test_cancelled_close_still_tears_the_conversation_down`, cancels `close()`
  mid-worker and asserts teardown still finishes on its own — the conversation
  and executor are closed and the lease is released — and fails (times out) on
  the pre-fix head.
- The bounded-drain timeouts have their own regressions:
  `test_worker_timeout_keeps_permit_until_the_worker_stops` and
  `test_interrupt_timeout_keeps_permit_until_the_interrupt_stops` hold a worker
  (and, in the second, its offloaded interrupt) past a shortened drain and
  assert a competing call is still 429, the conversation is still open and
  `close()` has not returned; releasing the work lets `close()` finish and the
  same call be admitted. Both fail (hang) on the pre-fix head, where the
  timeout released the permit and closed the conversation under the live work.
- `test_failed_teardown_continuation_is_reported_and_retryable` makes the
  parked continuation fail (a subscriber raising in `PubSub.close()`, injected
  so the continuation is already complete when `close()` inspects it) and
  asserts `close()` raises rather than reporting success with the conversation
  still open, that a retry then cleans up successfully without replaying the
  stale failure, and that cleanup ran only once. It fails on the pre-fix head,
  where `close()` skipped a completed continuation.
- `uv run pytest tests/agent_server tests/sdk/conversation tests/tools/terminal`
  -> passes locally (378 agent-server, 107 sdk/terminal) and in CI.
- `npm run check:agent-server-api` -> clean; `ruff check` / `ruff format --check`
  -> clean; OpenAPI export is byte-identical across two runs and passes the
  type-quality gate (64 allowlisted weak locations).
