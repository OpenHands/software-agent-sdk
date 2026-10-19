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

## Key decisions

- **The action is the tool's own model, not a dict.** `ExecuteToolRequest.action`
  is typed `Action` (the SDK discriminated union), so a malformed payload fails
  request validation as 422 instead of reaching the executor. The issue sketched
  `dict[str, Any]`; typing it is stricter and matches `RemoteConversation`'s
  signature.
- **The response carries the observation model, not text.** Tool-specific fields
  (`exit_code`, `command`, `content`) survive the round trip, so a caller can
  branch on the exit code of its bootstrap script. `is_error` is derivable from
  the observation, so no separate flag.
- **Unknown tool -> 422, unknown conversation -> 404.** `KeyError` from the tool
  lookup maps to 422 with the tool named; `None` from the service maps to 404.
- **Non-blocking.** `EventService` runs the call in an executor, matching
  `ask_agent`; a slow bootstrap script does not stall `/alive`.
- **No event appended, confirmation/analyzer bypassed.** Inherited from
  `LocalConversation.execute_tool()` by design; it is a pre-run setup mechanism,
  documented in the route docstring and the OpenAPI description.
- **Non-goal: auto-discovery.** The agent-server loads no project convention on
  its own (`.openhands/hooks.json` is read only via `POST /api/hooks`), so
  auto-running `<workspace>/.openhands/setup.sh` belongs to the client that owns
  the convention, not this server. That wiring lives in `OpenHands/OpenHands`.

## OpenAPI note for reviewers

Adding `action: Action` to a request body makes FastAPI split **every** action
schema into `<Name>-Input` / `<Name>-Output` twins (38 renames; 446 -> 486
schemas) — the same pattern `ConfirmationPolicyBase` already shows. Two
allowlisted weak-schema pointers therefore went stale; the allowlist entries were
renamed to their `-Input`/`-Output` twins (63 -> 65 entries). The REST breakage
gate flags `StartChildConversation*` oneOf additions **on the unmodified baseline
too**, i.e. pre-existing drift, not from this change.

## Evidence

- `control-agent-server map check` -> ok, 34 files, 942 ids.
- Live server (`control-agent-server exec`): `export` from one `execute_tool` is
  visible to the next in the same conversation (real `TerminalObservation`);
  unknown tool -> 422 naming it; no event added.
- `uv run pytest tests/agent_server tests/sdk/conversation` -> 3603 passed,
  12 failed (all `canvas_extensions/test_canvas_extension_backend.py`, a known
  container baseline failure).
- `tests/cross/test_remote_conversation_live_server.py -k execute_tool` -> 3 passed.
- `npm run check:agent-server-api` -> clean; ruff -> clean.
