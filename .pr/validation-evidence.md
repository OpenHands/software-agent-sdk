# Profile pre-flight validation: before/after evidence

Issue: https://github.com/OpenHands/software-agent-sdk/issues/5100 (filed by VascoSch92 before this contribution).

Baseline: `365ceb3a77f6dbe7b993041ddf04b9484c002e95`.
Environment: macOS, Python 3.13.12, dependencies installed from this repository's uv workspace via `make build`.

## Reproduce through the public HTTP endpoint

```sh
uv run pytest tests/cross/test_remote_conversation_live_server.py \
  -k profile_preflight_budget_error_over_http -q
```

This test starts an authenticated Agent Server on loopback and POSTs to `/api/profiles/over-budget/validate`. A separate local HTTP server returns a real provider-shaped `429` with `type: budget_exceeded`. LiteLLM and the SDK are not mocked. Credentials are synthetic, no paid provider or production state is used, and both servers shut down after the check.

Before the fix, the assertion fails:

```text
assert result["valid"] is False
E assert True is False
1 failed, 22 deselected
```

The local provider received **15 POST requests** before the invalid configuration was accepted. The reproduction sets SDK backoff waits to zero to avoid waiting two minutes; it preserves the five-attempt runtime policy, exposing the additional three provider-client attempts per SDK attempt.

## Focused regressions

```sh
uv run pytest tests/agent_server/test_profiles_router.py \
  -k 'rate_limits or does_not_wait_for_runtime_retries' -q
```

Before production edits: **14 failed**. These tests use the real SDK calls with only the provider transport replaced; they expose false positives, repeat calls, and the runtime retry delay in both chat completions and Responses.

## Candidate verification

The live HTTP regression now returns `valid=false`, identifies `LLMRateLimitError`, redacts the synthetic provider key, and records exactly **one provider POST**. Both chat completions and Responses have unit coverage for `budget_exceeded`, `insufficient_quota`, `usage_limit_reached`, recoverable `rate_limit_exceeded`, and quota codes retained only in exception context.

The preflight copies the submitted LLM configuration, disables SDK/provider retries and fallback, and applies a 10-second asynchronous deadline. Tests verify runtime configuration remains unchanged and a stalled provider is cancelled. The deadline bounds the endpoint's wait; an OAuth refresh already running in a worker thread cannot itself be forcibly stopped.

| Check | Result |
| --- | --- |
| `uv run pytest tests/agent_server -n 4 -q` | 2,198 passed |
| `uv run pytest tests/agent_server/test_profiles_router.py -q` | 126 passed |
| `TMPDIR=/tmp uv run pytest tests/cross -q` | 510 passed, 1 existing skip |
| `CI=true TMPDIR=/tmp uv run python -m pytest -m stress --durations=10 tests/agent_server/stress -q` | 13 passed |
| `uv run pre-commit run --all-files` | All checks passed, including Ruff and Pyright |
| `uv build --package openhands-agent-server` | Wheel and source distribution built |
| `git diff --check` | Passed |

The first cross-package run used the default macOS temporary directory and failed one existing gateway test because the tmux socket path exceeded the operating system's limit. The full suite passed with `TMPDIR=/tmp`; no test or product change was needed for that environment issue.

An independent verifier exercised eight real provider HTTP cases (chat/Responses × four quota/rate-limit codes); every case made one request in 0.01–0.19 seconds. The reviewer also found a subscription-token redaction gap in the new blocking error path. A new regression failed before its repair and passed afterward: exhausted subscription quotas now return a useful fixed explanation without copying potentially sensitive provider prose.

Independent re-verification tested four subscription quota/rate-limit cases, confirmed the transmitted OAuth credentials and retry/timeout settings, and checked both response and router INFO logs. Each case made one call in 0.003–0.187 seconds, the original configuration was unchanged, and no material review findings remained. All testing described here was performed by coding agents; no human testing is claimed. GitHub checks and maintainer review/merge remain external completion gates.
