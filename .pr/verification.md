# Bounded logging — #5373

Base: `b66c724361571aa5c982883173c71b04739b247d`.
Validation environments (all macOS arm64):

| Python | Rich | Result |
| --- | --- | --- |
| 3.13.5 | 14.3.3 (frozen lock) | 97 passed in 11.26s |
| 3.12.13 | 14.3.3 (frozen lock) | 97 passed in 11.19s |
| 3.12.13 | 15.0.0 (isolated environment override) | 97 passed in 11.54s |

The compatibility environment does not change the repository's lockfile or main venv.
No real model or provider calls were used.

## Before / after

1. Added `tests/sdk/logger/test_rich_traceback_limits.py` before changing production code.
   The `context_cycle` case failed with `subprocess.TimeoutExpired` after 15 seconds.
   The subprocess timeout kills and reaps the child rather than leaving a renderer running.
2. With the fix, all four subprocess cases return: context cycle, two-node cause cycle,
   a 2,000-exception chain, and repeatedly shared exception groups. Each keeps its log
   message, emits the omission marker, and produces under 4,096 stderr characters.
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
   `/alive`, `/server_info`, and the conversation REST endpoint. In the initial measured run:

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
# 97 passed in 11.26s after local refinement
```

Changed-file pre-commit checks pass: Ruff format/lint, pycodestyle, Pyright,
dynamic-attribute policy, import rules and tool registration. `git diff --check` passes.

## Design and limits

The Rich console handler preflights exception and traceback traversal with a budget
of 32 exception visits and 256 total frames. These are conservative internal ceilings,
not measured optimal thresholds: legitimate over-budget failures lose their Rich
traceback. Rich's existing display-frame limit does not bound frame extraction.

Visits include repeated references. Rich first invokes the standard-library formatter,
which can expand shared groups before Rich's own group deduplication applies. A
unique-node cache would therefore not bound all the formatting work. The preflight
also checks unsuppressed context independently of cause: the standard formatter can
follow context when a shared cause was already seen. Tests pin that case and exact
visit/frame boundaries, including aggregate frames spread across different exceptions.

Over-budget records are copied, then logged with the original message, exception type
and omission marker, without traceback or cached exception/stack text. The fallback
does not modify the original exception or the record received by other handlers.
Normal Rich formatting retains its standard record-cache behavior.

The WebSocket path retains `logger.exception` for ordinary diagnostic detail, without
the redundant `stack_info=True`; expected disconnects stay lightweight. Tests compare
normal causes, contexts and groups against unmodified Rich output, verify actual
JSON/CI formatting and disabled-Rich behavior, and assert the live server's omission
marker and typed error event. No environment knob, dependency, public API or persisted
schema changes; uvicorn routing is unchanged.

This preflight applies to the SDK-installed Rich handler, not JSON/file handlers or
application-installed handlers. It bounds exception/frame traversal for ordinary,
stable exception objects, not arbitrary user-defined `__str__`, source-file processing,
exception-text size, or slow output devices. The live tests use a synthetic exception
at a real service boundary, not the reporter's original Linux/Python 3.12/Rich 15.0.0
deployment or its 29-minute backlog. They do not prove how that deployment acquired
the cyclic exception. The full repository suite and Docker/binary builds were not run.

## Prior work

The issue reporter supplied the failure diagnosis. Charan Rathore's closed, unmerged
PR #5392 established the bounded-handler/record-copy approach and included live probe
results. This implementation follows that approach, adds a total frame budget and
visit accounting for shared graphs, and leaves the stock-entry-point live regression
in the permanent test suite. It is not presented as the first discovery of the fix.

An isolated comparison loaded the prior handler from #5392's diff, without changing
workspace source, and ran four selected current tests with that handler substituted:
three failed (single/aggregate frame ceilings and preserving a small shared group),
one passed (the shared-cause/context case). The old handler rejects repeated identities,
so it already avoids that context case by omitting the whole graph. The missed context
was a defect in this PR's initial visit-counting implementation, not a new flaw in #5392.

Two first-round independent source reviews identified the standard-formatter gap and
an unsafe in-process cyclic test. The gap now has a red/green regression; rendering of
unsafe cycles runs only in watchdog-protected subprocesses. The record-copy test uses
a finite over-budget group. Mutation checks deliberately removed the context check,
reset the frame count per exception, and modified the original exception's cause;
each caused its focused test to fail, with source restored byte-for-byte afterward.
A second-round independent source review found no remaining must-fix design defect.
Its suggestions tightened the synthetic-probe wording and added exact Rich-output
comparison for a genuinely raised exception chain, including traceback frames.
These were static reviews; test results above were obtained separately by execution.
