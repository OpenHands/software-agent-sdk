# Issue #5354: validation error batch preservation

Base: `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14`.
Implementation: `648fad5cc8802526b45be2d1e54b68f98c88fb14`.
Environment: macOS 26.7 arm64, Python 3.13.12, SDK 1.49.6,
LiteLLM 1.93.0, Pydantic 2.12.5 (workspace lockfile).

## Runtime reproduction

Run from the SDK checkout after `make build`:

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync python .pr/repro_mixed_validation.py --check --responses --anthropic
```

The script uses `Conversation.run()`, built-in `think`, and sequential execution.
The LLM transport returns valid A, invalid B (missing required `thought`), valid C.
The next request is captured at the transport boundary; an observer also captures
the common SDK history and delegates formatting to the real SDK implementation.
All synthetic prompts and tool outputs are safe to publish; no real credentials
or user conversation content are used.

- Base: exit 1, common history and next request contain `[A,B]` then `[C]`.
- Fix: exit 0, both contain `[A,B,C]`.
- Responses serialization changes from calls A/B, output B, call C, outputs A/C
  to calls A/B/C followed by all three outputs.
- LiteLLM Anthropic serialization on the base substitutes a skipped/interrupted
  result for A and drops the actual result. The fix retains actual A/C results
  and B's validation error after all tool-use blocks.

See [before.txt](before.txt) and [after.txt](after.txt) for full captured output.
Local checkout paths in logs are replaced with `<repo>`; payloads are unchanged.
To reproduce the baseline, copy this script outside the checkout before checking
out the base revision, then run the same command with the copied script path.

Only the LLM transport is scripted in the reproduction above. Separately,
[contributor-supplied live DeepSeek records](live_deepseek.md) exercise the next
request against DeepSeek V4.1 Flash through OpenRouter (thinking mode): base HTTP
400 in all three cases, fixed branch HTTP 200 in all three. The first completion
is scripted; the second is live. The supplied records were checked for matching
parameters, tool calls/results, and removed sensitive data; these API calls were
not repeated during this update. This demonstrates request acceptance, not a
complete conversation run or replay of the original production conversation.

Other providers were not called. Responses inspection above serializes the same
Chat-origin SDK history; it does not test a native Responses-origin response.

## Regression checks

Before implementation, the initial 32 batch cases yielded 28 failures and 4
passes (only invalid-last cases passed). After implementation all 32 passed.
Six additional cases exercise dispatch failure, cleanup callback failure, and
cancellation in sync/async dispatch while preserving the original exception.

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync pytest -q tests/sdk/agent tests/sdk/event tests/sdk/conversation
```

**1,956 passed, 6 deselected, 42 warnings**, including all 38 new cases.
[tests.txt](tests.txt) contains complete output, including an unawaited
`_apply_acp_model` coroutine warning at shutdown. This run is not warning-free.
Per-file pre-commit checks (Ruff, pycodestyle, Pyright, repository gates) passed.

## Compatibility and review

No event schema, stored-settings version, public signature, or provider-specific
formatting code changes. Errors move after all ActionEvents in the same response,
before confirmation/execution. Actions still reach the real callback immediately,
so stream identity is committed only after action emission succeeds. Previously
persisted malformed histories are not migrated or repaired.

This is a fork PR: remove temporary `.pr/` artifacts before merge. Preserve access
to reviewer evidence using commit-pinned links in the PR description. Keep the PR
Draft until the human-only description and maintainer readiness requirements are
complete. Maintainer integration-test coverage remains pending.
