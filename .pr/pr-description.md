<!-- Keep this PR as draft until it is ready for review. -->

<!-- AI/LLM agents:
Do not edit the HUMAN section.
-->

HUMAN:

<!--
Human author: please replace this comment with a short note (at least 20 visible
characters) before marking ready for review.
AI agents: you must not edit this section.
-->

---

AGENT:


## Why

When one LLM response contains valid calls A/C and an invalid call B, the event
log contains `Action A, Action B, Error B, Action C, Result A, Result C`.
Grouping only consecutive actions splits that response into two assistant
messages. This defect exists in the common SDK history, before provider-specific
serialization, even with sequential tool execution.

## Summary

- Group actions from the same response across their matching validation errors
  in `events_to_messages()`, then append those errors after the combined message.
- Prevent context-view cuts between actions from the same response when
  validation errors occur between them.
- Add 62 cases covering validation/execution errors, metadata, sync/async,
  confirmation, interruption, callback failures, and grouping/view boundaries.

The production change is confined to `event/base.py` and
`context/view/properties/batch_atomicity.py` (+24/−17 lines). Agent dispatch,
callback order, exception propagation, and event schemas are unchanged.

## Issue Number

Fixes #5354.

## How to Test

After `make build`:

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync python .pr/repro_mixed_validation.py --check --responses --anthropic

OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync pytest -q \
  tests/sdk/agent tests/sdk/event tests/sdk/conversation \
  tests/sdk/context/view tests/sdk/context/condenser tests/sdk/hooks tests/sdk/critic
```

The reproducer runs `Conversation.run()` with the real agent, built-in `think`
tool, event handling, and SDK formatters; only LLM transport is scripted. Base
`3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14` exits 1 with `[A,B] / [C]`;
the candidate exits 0 with `[A,B,C]` followed by all three actual results.
Responses serialization puts all calls before their outputs. LiteLLM Anthropic
serialization retains A's real result instead of replacing it with a
skipped/interrupted placeholder.

Local related suite: **2,398 passed, 6 deselected, 42 warnings**, plus an
unawaited `_apply_acp_model` warning at shutdown. Per-file pre-commit checks
passed. [Validation details](validation.md) and
[full test output](tests.txt) include commands, environment, coverage,
and the tested source manifest. This is not a full-repository test run.

[Contributor-supplied live DeepSeek reruns](live_deepseek.md)
returned **HTTP 200 for all three cases**, compared with baseline HTTP 400.
Each follow-up request contains one complete batch, its reasoning, and all actual
results. After temporary-path normalization, parameters, system/tools hashes,
user message, calls, and outputs match the baseline; only message grouping differs.
Event notification order remains unchanged.

The first completion is scripted; the second is sent to DeepSeek via OpenRouter.
This verifies request acceptance, not task completion: all returned responses
request another tool call, and ERROR at the configured two-iteration cap is
consistent with the SDK run-limit handler. The records do not capture the final
error-event payload or Git revision; the guide documents those limits and
raw-file provenance. No API calls were repeated during the evidence update.
Responses/Anthropic checks above remain local serialization checks, not live
provider or native Responses-origin tests.

## Video/Screenshots

No UI change. Runtime output: [before](before.txt) /
[after](after.txt).

## Design Doc

[Before/after design](design.html)
([HTML source](design.html)).

## Type

- [x] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Breaking change
- [ ] Docs / chore

## Notes

Companion documentation: https://github.com/OpenHands/docs/pull/848 (Draft; merge after the SDK fix).

Complete existing event histories with this interleaving also benefit when
projected again; no storage migration is needed. Missing results, incomplete
batches after process restart, and duplicate call IDs are outside this fix.
Confirmation and interruption tests resume the same conversation instance.

Keep Draft until the human note and maintainer readiness requirements are
satisfied. Candidate integration/evaluation workflows remain pending.
Remove temporary `.pr/` artifacts before merging this fork PR; retain the
commit-pinned review links.
