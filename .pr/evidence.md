# Evidence: closing a terminal stops `execute()` waiting on a running command

## New tests, before and after the fix (macOS, tmux 3.5 and subprocess backends)

`main` without the fix: all three fail, the waiting thread is still alive 10 s after `close()`.

```
$ uv run pytest tests/tools/terminal/test_terminal_session.py::test_closing_the_session_stops_waiting_on_a_running_command \
      tests/tools/terminal/test_pool_integration.py::TestPoolClose -q
E   AssertionError: execute() kept waiting on a command after close()      # [tmux]
E   AssertionError: execute() kept waiting on a command after close()      # [subprocess]
E   AssertionError: the call kept waiting after close()                    # pooled TerminalExecutor
```

With the fix:

```
3 passed in 12.23s
```

## Terminal suite on Linux (`python:3.12-slim` with tmux, curl, procps; how CI runs)

```
$ uv run pytest tests/tools/terminal -q -n 4
414 passed, 15 skipped, 2 failed
```

The 2 failures are `test_bash_background_server[subprocess]` and, without curl, `[tmux]`; both fail the same way on
`main` without this change. On macOS, 10 subprocess heredoc tests fail on `main` with and without it.

## End to end: an A2A agent running `LocalConversation.arun()` in a worker thread

The OpenHands agent for AgentEnv (scaleapi/agentenv-framework#281), SDK 1.57.0, model `claude-haiku-4-5` through a
LiteLLM gateway. The model runs `sleep 120` with a long `timeout`.

| | SDK 1.57.0 | SDK 1.57.0 with this fix |
|---|---|---|
| `docker stop -t 45` while the command runs | 45.3 s, exit 137 (SIGKILL) | 1.5 s, exit 0 |
| turn with `timeout_seconds: 8` | returns only when the command's timeout ends | `openhands.timeout` after 9 s, no `sleep` left |

Thread dump of the unfixed agent 40 s after SIGTERM: the tool call's thread is still in the wait loop.

```
Thread 0x0000ffff837ef180 (most recent call first):
  File ".../openhands/tools/terminal/terminal/terminal_session.py", line 775 in execute
  File ".../openhands/tools/terminal/impl.py", line 518 in _execute_pooled
  File ".../openhands/tools/terminal/impl.py", line 577 in __call__
  File ".../openhands/sdk/tool/tool.py", line 635 in __call__
  File ".../openhands/sdk/agent/agent.py", line 1534 in _execute_action_event
```
