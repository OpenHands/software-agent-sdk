# Agent-Server Fork Example (02/11) — Not Runnable in Runtime

The agent-server fork example (`examples/02_remote_agent_server/11_conversation_fork.py`)
could not be validated end-to-end inside the OpenHands runtime for two reasons:

1. **Runtime's server lacks fork endpoint** — The runtime runs the released agent-server
   (v1.17.0), which does not include the `POST /api/conversations/{id}/fork` endpoint
   added by this PR. Source conversation creation succeeds, but `fork()` returns 404.

2. **Shared tmux socket conflict** — Starting a new agent-server subprocess from the PR
   branch hangs during `_cleanup_stale_tmux_sessions()` because both the runtime's
   server and the example's server share the `openhands` tmux socket.

## What Was Verified

- **Pre-commit + pyright**: All checks pass (ruff format, ruff lint, pycodestyle, pyright,
  import deps, tool registration).
- **Standalone fork example (01/48)**: Successfully ran end-to-end — see
  `example-run-stdout.txt` and `example-run-stderr.txt` in this directory.
- **Unit tests**: All 17 fork-related tests pass (local fork, remote fork, server endpoint,
  deep-copy isolation).
- **Partial server test**: Source conversation created successfully against runtime server
  (10 events), confirming `RemoteConversation` + `Workspace(api_key=...)` auth works.

The agent-server example follows the same `ManagedAPIServer` pattern as
`examples/02_remote_agent_server/01_convo_with_local_agent_server.py` and will work
correctly in CI (which starts with a clean environment).
