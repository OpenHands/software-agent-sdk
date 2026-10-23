# Structured quota validation: before and after

Issue: [enyst/agent-sdk#40](https://github.com/enyst/agent-sdk/issues/40).
Tested product commit: `06a52395b85039fa5b9c9d0ce18f7085a8b942ab`.
Frozen failing commit: `5966c781ed014efc96c028a99bddd4ff78426741`.
The following commit adds only this reviewer evidence; product code stays at the
tested revision. Machine-readable excerpts are in
[quota-validation-evidence.json](quota-validation-evidence.json).

## Observed behavior

An HTTP 429 provider response containing `error.type` and `error.code` equal to
`insufficient_quota`, with the message “You exceeded your current quota, please
check your plan and billing details.”, previously passed profile validation.
LiteLLM replaced the outer exception's structured metadata and retained only
the human-readable message there. The original OpenAI exception still carried
the typed metadata in its exception chain.

The classifier now follows the preserved cause/context chain for exact known
codes and types. It honors suppressed contexts and guards against cycles.
Existing string compatibility remains, without adding message guesses.
Structured budget denial also keeps its existing stronger policy: stop retries
and model fallback. Provider quota exhaustion skips retries but allows fallback.

All four runs called `POST /api/profiles/qa-quota/validate` on a real Agent Server,
using its unmodified SDK/LiteLLM/OpenAI HTTP transport to a loopback error
provider. Each used isolated state and ports.

| Source | Fresh run | Quota verdict | Ordinary 429 verdict | Map result |
| --- | --- | --- | --- | --- |
| Frozen base | `20261023-060605-a477-map-f20` | `valid: true, error: null` | `valid: true` | xfail |
| Frozen base | `20261023-060618-fd05-map-f20` | `valid: true, error: null` | `valid: true` | xfail |
| Fixed commit | `20261023-070627-5271-map-f20` | `valid: false, error.type: LLMRateLimitError` | `valid: true` | xpass |
| Fixed commit | `20261023-070641-8fe2-map-f20` | `valid: false, error.type: LLMRateLimitError` | `valid: true` | xpass |

Every response above had HTTP status 200. Both fixed runs passed doctor with
`build_git_sha` equal to the tested product commit. Both recipe scripts exited
0, left the profile list unchanged, and stopped their servers. Their ports were
closed and server PIDs absent after cleanup. The CLI exits 1 for an `xpass`
because the maintenance recipe still marks this as a known bug. That marker
belongs to the separate maintenance PR, not this product fix.

## Reproduce

Run from a checkout of the verification harness in
[maintenance PR #43](https://github.com/enyst/agent-sdk/pull/43), with this fix in
a second checkout and `make build` completed there:

```sh
export PATH="$PWD/.agents/skills/verify-agent-server/scripts:$PATH"
docker-init -s -- control-agent-server map run \
  --file F20 --only F20.validate-quota-exhausted \
  --checkout /path/to/fix-checkout --fresh --repeat 2 --record --keep-going
```

The committed recipe includes both the transient positive control and the exact
quota body above. Its standard F20 setup requires the configured DeepSeek key,
but these validation checks make no paid model requests. `tmux` must be on PATH.
The subreaper wrapper is needed here because the cloud host's PID 1 does not
reap descendants; environments with a proper init may omit it.

## Regression tests and review

The new real HTTP retry regression failed before the patch: 8 failures and
12 passes across sync/async chat and Responses. All 20 cases pass after the
patch. Cases use structured code/type without machine codes in the message;
the transient control deliberately uses the same human quota wording.

The broader relevant suite passed **272 tests**:

```sh
uv run pytest tests/sdk/llm/test_budget_denial_http.py \
  tests/sdk/llm/test_exception_classifier.py \
  tests/sdk/llm/test_exception_mapping.py \
  tests/sdk/llm/test_llm_fallback.py \
  tests/agent_server/test_profiles_router.py -q
```

The permanent HTTP fallback cases cover immediate quota fallback, a fallback
model's own transient 429 retry, and no fallback on budget denial. Per-file
pre-commit hooks passed, including Ruff, pyright, and SDK dependency rules.
The suite emitted 31 dependency/deprecation warnings; none failed.
An independent agent reviewed the code and evidence and separately reproduced
the primary-quota → fallback-transient-429 → fallback-success path.

This proves deterministic failure classification through real transports, not
an external provider's billing behavior. No UI or public schema changed. Raw
private state and credentials are excluded from the published excerpts.
