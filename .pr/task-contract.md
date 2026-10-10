# Task contract: fast, accurate LLM profile validation

## Goal alignment
Get one useful OpenHands PR merged as GDemay. Authoritative requirement: https://github.com/OpenHands/software-agent-sdk/issues/5100; baseline main 365ceb3a77f6dbe7b993041ddf04b9484c002e95; root and agent-server AGENTS.md, CONTRIBUTING.md, repository review guide and CI workflows. This endpoint is owned by Agent Server and serves Agent Canvas. Stop only after the correct, independently verified change passes required CI, receives required review, and is merged. Deployment/package release is maintained upstream and is outside this PR's scope.

## Classification and scope
BUG. Limit changes to profile preflight validation and focused regression/live HTTP coverage. Keep request/response schemas and runtime LLM configuration unchanged. Preserve subscription restoration, completion/responses dispatch, ordinary rate-limit and timeout non-blocking behavior, and secret redaction. Do not change agent execution/retry defaults, release versions, or unrelated code. No duplicate implementation found across current open PRs and issue timeline.

## Acceptance criteria
- AC1: HTTP POST /api/profiles/{name}/validate returns valid=false and a redacted provider error for exhausted budget/quota 429 responses.
- AC2: Ordinary recoverable 429 remains valid=true.
- AC3: Validation makes at most two provider calls, returns within a short bounded timeout, and does not inherit or mutate runtime retry/timeout settings.
- AC4: Focused agent-server unit coverage exercises these cases; live HTTP evidence covers the real LLM transport and route.
- AC5: Existing validation/subscription tests and relevant server checks pass. Required CI passes on the PR head and an independent verifier finds no material issue.

## Baseline and evidence
The authenticated live HTTP regression failed on the baseline before product edits: a local provider's budget-exceeded 429 was accepted after 15 POSTs. See [validation evidence](validation-evidence.md). Tests use synthetic credentials and local deterministic providers; no paid inference, real secrets, or production state.

## Risk and release
Main risks: misclassifying transient limits, accidental runtime retry changes, secret exposure, regression to subscription auth. Preserve defaults outside the temporary preflight LLM. Normal upstream PR merge/release; revert this scoped change if harmful. User authorizes GitHub issue/PR communications and fixes through merge. Respect repository human-only PR note and required independent approval; never fabricate human testing or bypass branch protections.

## Verification log
Local regression, integration, stress, lint/type, package build, and independent review evidence is recorded in [validation-evidence.md](validation-evidence.md). Required GitHub checks and maintainer merge remain pending.

2026-09-17 baseline evidence: authenticated live Agent Server + loopback HTTP provider test, `uv run pytest tests/cross/test_remote_conversation_live_server.py -k profile_preflight_budget_error_over_http -q`, failed as intended: provider budget_exceeded 429 returned valid=true. Even with SDK backoff waits set to zero to accelerate reproduction, 15 provider POSTs occurred (5 SDK attempts × 3 provider-client attempts). Log: /tmp/openhands-sdk-live-red.log. The real LiteLLM path drops structured error codes from the mapped error message, so inspecting only the message marker is insufficient.

Unit RED before source edits: `uv run pytest tests/agent_server/test_profiles_router.py -k 'rate_limits or does_not_wait_for_runtime_retries' -q` yielded 14 intended failures. Log: /tmp/openhands-preflight-red.log. Tests cover both completion/Responses transport paths, quota errors, recoverable 429s, and runtime retry delay isolation.

User clarified that working on an issue created by us is forbidden. Selected issue #5100 pre-existed this session and was filed by maintainer VascoSch92. We created no issue; we posted only a claim comment on #5100.

Independent review found that the newly blocking subscription quota path could expose an OAuth token in returned provider prose. Reproduced this before repair. Use a safe quota-specific explanation for this path without reading or refreshing credentials during error handling; preserve existing diagnostics elsewhere.
