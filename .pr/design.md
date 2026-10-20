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
back into the tool's own observation model.

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
  an idle eviction, a run or another direct call cannot overlap it. `run()` while
  a direct call is in flight is 409, and a second direct call is 409. The flag is
  released on success, on a validation failure, and, for a cancelled
  request whose synchronous worker already started, only when that worker
  actually exits, since cancellation cannot stop it. `close()` interrupts the
  running command and waits for its worker before closing the executors, so
  teardown cannot pull a terminal out from under a command mid-run.
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
  against **that tool's** `action_type`: a mismatch is a 422, not a 500.
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

- `control-agent-server map check` -> ok, 34 files, 945 ids.
- Live server (`control-agent-server exec` / `pytest tests/cross/...live_server.py
  -k execute_tool`) -> 5 passed: an `export` from one `execute_tool` is visible to
  the next in the same conversation (real `TerminalObservation`), a completed call
  is admitted again, a `GrepAction` with `tool_name="terminal"` is 422, an unknown
  tool is 422 naming it, an unknown id is 404, an unauthenticated call is 401, and
  a registered secret is masked.
- `uv run pytest tests/agent_server tests/sdk/conversation tests/tools/terminal`
  -> passes (see CI).
- `npm run check:agent-server-api` -> clean; `ruff check` / `ruff format --check`
  -> clean; OpenAPI export is byte-identical across two runs and passes the
  type-quality gate (64 allowlisted weak locations).
