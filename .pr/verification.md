# Bounded logging — #5373

Base: `b66c724361571aa5c982883173c71b04739b247d`.
Environment: macOS arm64, Python 3.13.5, the frozen workspace lock (Rich 14.3.3).
No real model or provider calls were used.

## Before / after

1. Added `tests/sdk/logger/test_rich_traceback_limits.py` before changing production code.
   The `context_cycle` case failed with `subprocess.TimeoutExpired` after 15 seconds.
   The subprocess timeout kills and reaps the child rather than leaving a renderer running.
2. With the fix, all four subprocess cases return: context cycle, two-node cause cycle,
   a 2,000-exception chain, and repeatedly shared exception groups. Each keeps its log
   message, emits the omission marker, and produces under 4 KiB of stderr.
3. Ran `tests/agent_server/test_logging_live_server.py` through the stock
   `python -m openhands.agent_server` entry point. A test-only module loaded through
   `--import-modules` injects a cyclic exception at `EventService.send_message`.
   Conversation creation, HTTP, WebSocket, logging configuration and signal handling
   are real; the service failure is synthetic.
4. Temporarily restored both production files (`logger.py`, `sockets.py`) to the base
   revision and repeated the `socket` live test. The error event arrived, but the next
   HTTP probe failed with `httpx.ReadTimeout`. Cleanup had to kill the child after
   its SIGTERM grace period. The test failed in 11.31 seconds. Restored both edited
   files byte-for-byte in `finally`.
5. The fixed live tests both pass. Each sends five messages, receives five
   `ServerErrorEvent` replies, and gets 200 responses from all fifteen probes to
   `/alive`, `/server_info`, and the conversation REST endpoint. In the measured run:

   ```text
   socket: 5 error replies, 15 HTTP probes OK, RSS delta=491520; SIGTERM=-15
   handler: 5 error replies, 15 HTTP probes OK, RSS delta=524288; SIGTERM=-15
   ```

   The `handler` case additionally logs the cyclic exception on the event loop before
   raising it into the WebSocket path, so removing tracebacks at the socket call site
   alone cannot satisfy the test. Both children honor SIGTERM within five seconds.

## Commands

```sh
uv sync --frozen --dev
LITELLM_LOCAL_MODEL_COST_MAP=true uv run --frozen pytest \
  tests/sdk/logger/ \
  tests/agent_server/test_logging_live_server.py \
  tests/agent_server/test_sockets_service_getters.py \
  tests/agent_server/test_event_router_websocket.py \
  tests/agent_server/test_websocket_first_message_auth.py \
  tests/agent_server/test_session_socket.py -q
# 89 passed in 11.19s
```

Changed-file pre-commit checks pass: Ruff format/lint, pycodestyle, Pyright,
dynamic-attribute policy, import rules and tool registration. `git diff --check` passes.

## Design and limits

The Rich console handler preflights exception and traceback traversal with a budget
of 32 exception visits and 256 total frames. Visits include repeated references;
therefore both cycles and expansion of shared groups are bounded. Small shared groups
remain renderable. The exact budget boundaries are covered by tests.

Over-budget records are copied, then logged with the original message, exception type
and omission marker, without traceback or cached exception/stack text. Neither the
exception graph nor the record seen by other handlers is modified. Ordinary Rich
tracebacks, `LOG_RICH_TRACEBACKS=false`, JSON/CI handlers and uvicorn routing remain
unchanged. No environment knob, dependency, public API or persisted schema changes.

This bounds the reported exception-graph/frame traversal, not arbitrary user-defined
`__str__` execution or slow output devices. The live tests use a synthetic exception
at a real service boundary, not the reporter's original Linux/Python 3.12/Rich 15.0.0
deployment or its 29-minute backlog. They do not prove how that deployment acquired
the cyclic exception. The full repository suite and Docker/binary builds were not run.

## Prior work

The issue reporter supplied the failure diagnosis. Charan Rathore's closed, unmerged
PR #5392 established the bounded-handler/record-copy approach and included live probe
results. This implementation follows that approach, adds a total frame budget and
visit accounting for shared graphs, and leaves the stock-entry-point live regression
in the permanent test suite. It is not presented as the first discovery of the fix.
