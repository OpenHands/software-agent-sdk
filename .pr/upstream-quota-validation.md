# Upstream quota validation migration (2026-10-24)

Issue: [OpenHands/software-agent-sdk#6185](https://github.com/OpenHands/software-agent-sdk/issues/6185).
Original report: [enyst/agent-sdk#40](https://github.com/enyst/agent-sdk/issues/40).
Original fix: [enyst/agent-sdk#47](https://github.com/enyst/agent-sdk/pull/47).

The original commits were cherry-picked with `-x`, preserving Engel Nyst's
authorship and the OpenHands co-author trailer:

- `06a52395b85039fa5b9c9d0ce18f7085a8b942ab` ->
  `f3619d0d86aa655793e12115d2958668fcf244ce` (product and HTTP regression tests).
- `fd5d519e14c9f6bf8c0ad7177d80429fb3147d8b` ->
  `d919407e0afaf50d053978d08ea951d43db2b0d5` (original evidence).

Base: upstream `acd96e6fa424c02432304395b9208f99be24b54d`.
Product tested after migration: `d919407e0afaf50d053978d08ea951d43db2b0d5`.
This additional evidence commit changes no product code.

## Real profile endpoint

The [reproducer](reproduce_profile_quota.py) starts a fresh Agent Server process
using the production `create_app()` factory and a real loopback HTTP provider.
It calls `POST /api/profiles/qa-quota/validate` through HTTP. The SDK, LiteLLM,
and OpenAI transports are unpatched. Only the provider response is deterministic.
The provider returns the same human-readable quota message for both cases;
only its structured `code` and `type` change.

| Provider code/type | Upstream base | Migrated fix |
| --- | --- | --- |
| `rate_limit_exceeded` | HTTP 200, `valid: true` | HTTP 200, `valid: true` |
| `insufficient_quota` | HTTP 200, `valid: true` | HTTP 200, `valid: false`, `LLMRateLimitError` |

Both runs made exactly one provider request per validation, left the profile
store unchanged, stopped the server, and closed its listening port. Sanitized
responses and dependency versions are in
[upstream-quota-validation.json](upstream-quota-validation.json).

From the desired product checkout, after `make build`, run the same script:

```sh
.venv/bin/python /path/to/reproduce_profile_quota.py --output /tmp/quota-result.json
```

To reproduce the base, use the script from this branch with an isolated checkout
of the base commit. Run from that checkout with its own virtual environment.
The script records the checkout's HEAD and the installed package versions.

Environment: macOS, Python 3.13.11, SDK / Agent Server 1.58.1, LiteLLM 1.93.0,
OpenAI 2.33.0. Each run used isolated temporary state, fabricated keys, and
loopback-only servers. No live provider billing account was exercised.

## Regression checks

The new `test_http_structured_quota_skips_retries` cases, run against unmodified
upstream product code, yielded **8 failed / 12 passed**. The eight failures are
structured quota/budget errors on sync/async chat. Responses and transient
controls already passed on the base.

The migrated branch passed **280 tests**:

```sh
uv run pytest tests/sdk/llm/test_budget_denial_http.py \
  tests/sdk/llm/test_exception_classifier.py \
  tests/sdk/llm/test_exception_mapping.py \
  tests/sdk/llm/test_llm_fallback.py \
  tests/agent_server/test_profiles_router.py -q
```

These include ordinary HTTP rate-limit retries, quota-to-fallback behavior,
the fallback's own transient retry, explicit budget denial blocking fallback,
sync/async chat and Responses, and profile routes. The run emitted 32 existing
dependency/deprecation warnings.

`make build` and changed-file pre-commit checks passed, including Ruff, pyright,
and dependency rules. Source-PR CI was inspected separately: its non-test
checks passed, but the `Run tests` jobs were cancelled after 24 hours. Those old
checks are not claimed as validation of this migration.

## Duplicate and scope review

Current open and closed upstream searches for `insufficient_quota`, quota
titles, profile validation, and structured quota found no equivalent open
issue or PR before #6185 was filed. Closed #5100 / merged #5768 introduced
profile quota validation but did not cover the structured HTTP normalization
gap. Closed #4936 / merged #4917 and merged #5309 establish the retry/fallback
policies preserved here. Open #6030 upgrades LiteLLM for DecisionModel support;
it does not change this classifier.

No public schema, persistence format, dependency, or verification-harness
change is part of this migration.
