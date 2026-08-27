# PR #4204 — live validation evidence

Temporary PR evidence for `ci: enable API compliance and condenser test labels`
(branch `openhands/enable-special-integration-test-labels`).

This directory is `.pr/` by convention: temporary, local-only (excluded via
`.git/info/exclude`), holding live-run scripts and their results so a reviewer
can see what actually happens when the workflow's routed runners execute.

## What the PR does

`.github/workflows/integration-runner.yml` re-wires two labels whose runners
still live in the repo but whose trigger workflows were removed by #3974:

- **`api-compliance-test`** → a new independent job runs
  `tests/integration/api_compliance/run_compliance.py`.
- **`condenser-test`** → the existing `c##_*` runner via `--test-type condenser`,
  with a two-model default matrix (`gpt-5.5,claude-sonnet-4-6`).

Because the PR is CI-only, "live proof" means running the underlying runners the
labels route to. Both were exercised locally against the eval LLM proxy
(`https://llm-proxy.eval.all-hands.dev`).

## 1. api-compliance-test — live LLM completion ✅

A single, tiny live run: one malformed-history pattern against one model.

```bash
export LLM_API_KEY=$LITELLM_API_KEY
export LLM_BASE_URL=https://llm-proxy.eval.all-hands.dev
uv run python tests/integration/api_compliance/run_compliance.py \
  --patterns unmatched_tool_use \
  --models claude-sonnet-4-5 \
  --output-dir .pr/compliance-live
```

Result (full report: `api_compliance_live_report.md` / `.json`):

- Real completion call to `litellm_proxy/claude-sonnet-4-5-20250929`, duration 3.2s.
- Pattern `unmatched_tool_use` (a `tool_use` block with no following `tool_result`)
  was **REJECTED** by the API with HTTP 400 →
  `LLMMalformedConversationHistoryError`. That is the expected outcome for
  malformed input, and it proves the compliance runner the job invokes works
  end-to-end against a live provider.
- Summary: Total 1, Rejected 1, Accepted 0.

This is the exact command the new `run-api-compliance-tests` job runs (minus the
`--patterns`/`--models` narrowing used here to keep it to a single completion).

## 2. condenser-test — routing selection (no LLM burn) ✅

The condenser tests are full multi-turn agent loops (e.g. `c05` drives ~50 echo
tool-calls), so they are intentionally *not* run here under the "tiny" constraint.
Instead the routing the label depends on was verified with zero LLM calls:

```bash
uv run python -c "from tests.integration.run_infer import load_integration_tests; ..."
```

`--test-type condenser` correctly discovers and filters to the full suite:

```
c01_thinking_block_condenser, c02_hard_context_reset, c03_delayed_condensation,
c04_token_condenser, c05_size_condenser
```

Full end-to-end condenser runs against live models are already recorded in the
PR's "How to Test" section (linked Actions runs).

## Notes

- Uses the local eval proxy key; no secrets are written to these files.
- `.pr/` is excluded from git locally; commit with `git add -f` only if you want
  reviewers to see it in the PR diff.
