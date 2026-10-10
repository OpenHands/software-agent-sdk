# Linux CI follow-up: server exit response

Initial PR head `bf43d5c2079cc6a437035b4c78bcca8c9cb22348` passed the local terminal suite, but the real Linux tools CI encountered another tmux shutdown response in the existing `exit 7` workflow:

```text
TerminalExecutor.__call__ → _execute_pooled → TerminalSession.execute
→ TmuxTerminal.read_screen → LibTmuxException(['server exited unexpectedly'])
```

CI evidence: https://github.com/OpenHands/software-agent-sdk/actions/runs/37575831161/job/112644397693

This is not a test timeout or an assertion relaxed by the patch: the error was escaping the executor because the recovery classifier did not recognize that response. Added its exact text to the recoverable server-loss markers. The same interrupted-command/no-replay contract applies.

The fault-injected regression case fails before the marker is added (1 failed, 1 passed) and passes afterwards. The existing real shell-exit tests remain unchanged and enabled. Updated focused suites: **41 passed**; pre-commit including Pyright passes. CI is rerun on this follow-up head; the original 357-pass full local run and 40-pass libtmux 0.62.0 run apply to the initial head, not claimed as full current-head validation.
